#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from unittest import mock

from validate_contract import build_private_inputs, canonical


SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from taey_apply import preparer
from taey_apply.contract import IntakeContractError


def sha256(raw_bytes: bytes) -> str:
    return hashlib.sha256(raw_bytes).hexdigest()


def environment() -> dict[str, str]:
    value = dict(os.environ)
    value["PYTHONPATH"] = str(SOURCE_ROOT)
    return value


def write_draft(path: Path, raw_bytes: bytes, *, mode: int = 0o400) -> Path:
    path.parent.mkdir(mode=0o700, exist_ok=True)
    path.parent.chmod(0o700)
    path.write_bytes(raw_bytes)
    path.chmod(mode)
    return path


def command(root: Path, draft: Path, seat: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "taey_apply.prepare_cli",
        "--private-root",
        str(root),
        "--draft-file",
        str(draft),
        "--seat-id",
        seat,
        "--correlation-id",
        seat,
    ]


def invoke_success(root: Path, draft: Path, seat: str) -> dict[str, object]:
    completed = subprocess.run(
        command(root, draft, seat),
        check=True,
        capture_output=True,
        text=True,
        env=environment(),
    )
    if completed.stderr:
        raise RuntimeError("preparer wrote unexpected stderr")
    return json.loads(completed.stdout)


def invoke_failure(
    root: Path,
    draft: Path,
    seat: str,
    expected_failure_code: str,
) -> None:
    completed = subprocess.run(
        command(root, draft, seat),
        capture_output=True,
        text=True,
        env=environment(),
    )
    expected_stderr = (
        f"IntakeContractError[{expected_failure_code}]: preparation stopped\n"
    )
    if (
        completed.returncode != 2
        or completed.stdout
        or completed.stderr != expected_stderr
    ):
        raise RuntimeError(f"adversarial case {seat} did not stop exactly")


def assert_terminal_refusal(
    root: Path,
    draft: Path,
    seat: str,
    failure_code: str,
) -> None:
    marker = root / "receipts" / seat / f"{seat}.json"
    marker_bytes = marker.read_bytes()
    value = json.loads(marker_bytes)
    expected = {
        "correlation_id": seat,
        "failure_code": failure_code,
        "ok": False,
        "schema": "taey_apply_linkedin_intake_preparation_refusal_v1",
        "seat_id": seat,
        "state": "preparation_refused",
    }
    if (
        value != expected
        or marker_bytes != canonical(expected)
        or marker_bytes.endswith(b"\n")
        or stat.S_IMODE(marker.stat().st_mode) != 0o400
        or any(
            stat.S_IMODE((root / bucket / seat).stat().st_mode) != 0o700
            for bucket in ("transactions", "claims", "receipts")
        )
        or (root / "claims" / seat / f"{seat}.json").exists()
    ):
        raise RuntimeError(f"adversarial case {seat} lacks terminal evidence")
    invoke_failure(root, draft, seat, "identity_spent")
    if marker.read_bytes() != marker_bytes:
        raise RuntimeError(f"adversarial case {seat} changed on refused reuse")


def run_success_and_replay(root: Path, source_transaction: dict[str, object]) -> None:
    raw_draft = json.dumps(source_transaction, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"
    draft = write_draft(root / "drafts" / "success.json", raw_draft)
    seat = "prepared-success"
    result = invoke_success(root, draft, seat)
    target = root / "transactions" / seat / f"{seat}.json"
    target_bytes = target.read_bytes()
    expected_bytes = canonical(source_transaction)
    parent_modes = {
        bucket: stat.S_IMODE((root / bucket / seat).stat().st_mode)
        for bucket in ("transactions", "claims", "receipts")
    }
    public_result = json.dumps(result, sort_keys=True)
    forbidden_values = (
        "Example mounted card",
        "Example role",
        "Example company",
        "opaque-search-reference",
        "linkedin.com",
        str(root),
    )
    if (
        result.get("schema") != "taey_apply_linkedin_intake_preparation_v1"
        or result.get("state") != "prepared_unclaimed"
        or result.get("transaction_sha256") != sha256(expected_bytes)
        or result.get("draft_sha256") != sha256(raw_draft)
        or result.get("canonicalization_changed") is not True
        or result.get("canonical_no_trailing_newline") is not True
        or result.get("claim_absent") is not True
        or result.get("receipt_absent") is not True
        or target_bytes != expected_bytes
        or target_bytes.endswith(b"\n")
        or stat.S_IMODE(target.stat().st_mode) != 0o400
        or set(parent_modes.values()) != {0o700}
        or (root / "claims" / seat / f"{seat}.json").exists()
        or (root / "receipts" / seat / f"{seat}.json").exists()
        or any(value in public_result for value in forbidden_values)
    ):
        raise RuntimeError("successful preparation postcondition failed")
    before = target.read_bytes()
    invoke_failure(root, draft, seat, "identity_spent")
    if target.read_bytes() != before:
        raise RuntimeError("spent identity changed after refusal")


def run_postwrite_finalization_failures(
    root: Path, source_transaction: dict[str, object]
) -> int:
    original_directory_metadata = preparer._directory_metadata
    cases = (
        (
            "postwrite-contract-failure",
            IntakeContractError(
                "unsafe_private_path", "injected final parent validation failure"
            ),
            "unsafe_private_path",
        ),
        (
            "postwrite-oserror-failure",
            OSError("injected final parent validation failure"),
            "preparation_write_indeterminate",
        ),
    )

    for seat, injected_failure, expected_failure_code in cases:
        draft = write_draft(
            root / "drafts" / f"{seat}.json", canonical(source_transaction)
        )
        target = root / "transactions" / seat / f"{seat}.json"
        marker = root / "receipts" / seat / f"{seat}.json"

        def fail_final_parent_validation(path: Path, context: str) -> os.stat_result:
            if context == "receipts parent" and target.exists():
                raise injected_failure
            return original_directory_metadata(path, context)

        try:
            with mock.patch.object(
                preparer,
                "_directory_metadata",
                side_effect=fail_final_parent_validation,
            ):
                preparer.prepare_linkedin_intake(
                    private_root_value=root,
                    draft_path_value=draft,
                    seat_id_value=seat,
                    correlation_id_value=seat,
                )
        except IntakeContractError as exc:
            if exc.failure_code != expected_failure_code:
                raise RuntimeError("postwrite failure changed failure code") from exc
        else:
            raise RuntimeError("postwrite finalization failure did not stop")

        marker_bytes = marker.read_bytes()
        marker_value = json.loads(marker_bytes)
        if (
            not target.exists()
            or stat.S_IMODE(target.stat().st_mode) != 0o400
            or marker_value.get("schema")
            != "taey_apply_linkedin_intake_preparation_refusal_v1"
            or marker_value.get("failure_code") != expected_failure_code
            or marker_bytes != canonical(marker_value)
            or marker_bytes.endswith(b"\n")
            or stat.S_IMODE(marker.stat().st_mode) != 0o400
            or not marker.exists()
            or (root / "claims" / seat / f"{seat}.json").exists()
        ):
            raise RuntimeError("postwrite failure lacks authoritative terminal marker")

        before = marker.read_bytes()
        invoke_failure(root, draft, seat, "identity_spent")
        if marker.read_bytes() != before:
            raise RuntimeError("postwrite failure marker changed on preparer refusal")
    return len(cases)


def run_draft_failures(root: Path, source_transaction: dict[str, object]) -> int:
    canonical_draft = canonical(source_transaction)
    cases: list[tuple[str, bytes, str, int]] = []
    duplicate = canonical_draft[:-1] + (
        b',"schema":"taey_apply_linkedin_intake_private_input_v1"}'
    )
    cases.append(("duplicate-key", duplicate, "private_input_invalid", 0o400))
    nan_value = dict(source_transaction)
    nan_raw = canonical(nan_value).replace(
        b'"card_digest":"', b'"card_digest":NaN,"ignored":"'
    )
    cases.append(("nan", nan_raw, "private_input_invalid", 0o400))
    cases.append(("non-object", b"[]", "private_input_invalid", 0o400))
    extra = {**source_transaction, "unexpected": True}
    cases.append(("extra-field", canonical(extra), "private_input_invalid", 0o400))
    unsafe = {**source_transaction, "search_artifact_ref": "../outside.json"}
    cases.append(("unsafe-ref", canonical(unsafe), "private_input_invalid", 0o400))
    mismatch = {**source_transaction, "card_digest": "0" * 64}
    cases.append(("pairing-mismatch", canonical(mismatch), "pair_mismatch", 0o400))
    cases.append(("wrong-mode", canonical_draft, "unsafe_private_path", 0o600))
    for seat, raw_bytes, code, mode in cases:
        draft = write_draft(root / "drafts" / f"{seat}.json", raw_bytes, mode=mode)
        invoke_failure(root, draft, seat, code)
        assert_terminal_refusal(root, draft, seat, code)
    valid = write_draft(root / "drafts" / "valid-link-target.json", canonical_draft)
    symlink = root / "drafts" / "symlink.json"
    symlink.symlink_to(valid.name)
    invoke_failure(root, symlink, "symlink", "unsafe_private_path")
    assert_terminal_refusal(root, symlink, "symlink", "unsafe_private_path")
    missing = root / "drafts" / "missing.json"
    invoke_failure(root, missing, "missing-draft", "unsafe_private_path")
    assert_terminal_refusal(
        root, missing, "missing-draft", "unsafe_private_path"
    )
    outside = write_draft(
        root.parent / "outside-draft.json", canonical_draft
    )
    invoke_failure(root, outside, "outside-draft", "unsafe_private_path")
    assert_terminal_refusal(
        root, outside, "outside-draft", "unsafe_private_path"
    )
    return len(cases) + 3


def run_unaccepted_identity_cases(
    root: Path, source_transaction: dict[str, object]
) -> int:
    draft = write_draft(root / "drafts" / "unaccepted.json", canonical(source_transaction))
    invalid_seat = "invalid/seat"
    invoke_failure(root, draft, invalid_seat, "private_input_invalid")
    if any((root / bucket / invalid_seat).exists() for bucket in ("transactions", "claims", "receipts")):
        raise RuntimeError("invalid identity created state")

    invalid_root = root.parent / "wrong-mode-private"
    invalid_root.mkdir(mode=0o700)
    invalid_draft = write_draft(
        invalid_root / "drafts" / "invalid-root.json", canonical(source_transaction)
    )
    invalid_root.chmod(0o755)
    invoke_failure(invalid_root, invalid_draft, "invalid-root", "unsafe_private_path")
    if any((invalid_root / bucket).exists() for bucket in ("transactions", "claims", "receipts")):
        raise RuntimeError("invalid root created identity state")
    return 2


def run_existing_identity_case(root: Path, source_transaction: dict[str, object]) -> None:
    seat = "existing-identity"
    existing = root / "receipts" / seat
    existing.mkdir(mode=0o700)
    draft = write_draft(root / "drafts" / "existing.json", canonical(source_transaction))
    invoke_failure(root, draft, seat, "identity_spent")
    if (root / "transactions" / seat).exists() or (root / "claims" / seat).exists():
        raise RuntimeError("existing identity refusal created sibling parents")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="taey-apply-preparer-") as temporary:
        root = Path(temporary) / "private"
        root.mkdir(mode=0o700)
        root.chmod(0o700)
        build_private_inputs(root)
        source_path = root / "transactions" / "seat" / "intake.json"
        source_transaction = json.loads(source_path.read_text(encoding="utf-8"))
        run_success_and_replay(root, source_transaction)
        postwrite_failures = run_postwrite_finalization_failures(
            root, source_transaction
        )
        adversarial_cases = run_draft_failures(root, source_transaction)
        run_existing_identity_case(root, source_transaction)
        unaccepted_cases = run_unaccepted_identity_cases(root, source_transaction)
        print(
            json.dumps(
                {
                    "schema": "taey_apply_preparer_gate_v1",
                    "successful_preparations": 1,
                    "replay_refusals": 1,
                    "adversarial_cases": (
                        adversarial_cases + postwrite_failures + 1 + unaccepted_cases
                    ),
                    "terminal_refusal_replays": (
                        adversarial_cases + postwrite_failures
                    ),
                    "postwrite_terminal_markers": postwrite_failures,
                    "presence_receipt_precheck_refusals": postwrite_failures,
                    "canonical_no_trailing_newline": True,
                    "private_values_exposed": False,
                    "verdict": "PASS",
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
