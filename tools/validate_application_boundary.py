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
from typing import Any


SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from taey_apply.application_confirmation import EmployerConfirmation  # noqa: E402
from taey_apply.application_contract import (  # noqa: E402
    ApplicationContractError,
    EVIDENCE_STATES,
    OneActionOutcome,
    OneActionRequest,
)
from taey_apply.application_preparer import prepare_application  # noqa: E402
from taey_apply.application_runner import run_application  # noqa: E402
from taey_apply.contract import canonical_json_bytes  # noqa: E402


PRIVATE_SENTINEL = "synthetic-private-applicant-value"


def digest(value: str | bytes) -> str:
    raw_bytes = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(raw_bytes).hexdigest()


def write_frozen(path: Path, value: dict[str, Any]) -> tuple[Path, str]:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    raw_bytes = canonical_json_bytes(value)
    path.write_bytes(raw_bytes)
    path.chmod(0o400)
    return path, digest(raw_bytes)


def build_lifecycle(
    root: Path,
    seat: str,
    *,
    maximum_one_action_calls: int = 4,
    bad_stage: str | None = None,
    extra_field: tuple[str, object] | None = None,
) -> tuple[Path, str, str]:
    application_identity_sha256 = digest(f"application:{seat}")
    evidence: dict[str, dict[str, str]] = {}
    for stage, expected_state in EVIDENCE_STATES.items():
        schema = f"taey_apply_{stage}_gate_receipt_v1"
        receipt_ref = f"application-evidence/{seat}/{stage}.json"
        actual_state = "wrong_state" if stage == bad_stage else expected_state
        _, receipt_sha256 = write_frozen(
            root / receipt_ref,
            {
                "schema": schema,
                "ok": True,
                "state": actual_state,
                "application_identity_sha256": application_identity_sha256,
                "evidence_sha256": digest(f"{seat}:{stage}:evidence"),
            },
        )
        evidence[stage] = {
            "receipt_ref": receipt_ref,
            "receipt_sha256": receipt_sha256,
            "receipt_schema": schema,
            "expected_state": expected_state,
        }
    context_ref = f"application-contexts/{seat}.json"
    context_schema = "taey_private_application_context_v1"
    _, context_sha256 = write_frozen(
        root / context_ref,
        {
            "schema": context_schema,
            "private_value": PRIVATE_SENTINEL,
            "truthful": True,
        },
    )
    lifecycle: dict[str, Any] = {
        "schema": "taey_apply_application_lifecycle_v1",
        "operation": "execute_autonomous_application",
        "provider": "greenhouse",
        "application_identity_sha256": application_identity_sha256,
        "evidence": evidence,
        "application_context_ref": context_ref,
        "application_context_sha256": context_sha256,
        "application_context_schema": context_schema,
        "maximum_one_action_calls": maximum_one_action_calls,
        "envelope_ref": f"application-envelopes/{seat}/{seat}.json",
        "result_ref": f"application-results/{seat}/{seat}.json",
        "refusal_ref": f"application-preparation-refusals/{seat}/{seat}.json",
    }
    if extra_field is not None:
        lifecycle[extra_field[0]] = extra_field[1]
    return (
        *write_frozen(root / "application-lifecycles" / f"{seat}.json", lifecycle),
        application_identity_sha256,
    )


class SuccessfulExecutor:
    def __init__(self) -> None:
        self.requests: list[OneActionRequest] = []

    def __call__(self, request: OneActionRequest) -> OneActionOutcome:
        self.requests.append(request)
        receipt_sha256 = digest(f"executor-receipt:{request.sequence_number}")
        common = {
            "application_identity_sha256": (
                request.envelope.application_identity_sha256
            ),
            "action_id": f"action-{request.sequence_number}",
            "previous_receipt_sha256": request.previous_receipt_sha256,
            "receipt_sha256": receipt_sha256,
        }
        if request.sequence_number == 1:
            return OneActionOutcome(
                **common,
                state="observation_proven",
                mutation_count=0,
                postcondition_sha256=digest("initial-form-observed"),
                next_mutation_authorized=True,
            )
        if request.sequence_number == 2:
            return OneActionOutcome(
                **common,
                state="action_proven",
                mutation_count=1,
                postcondition_sha256=digest("field-postcondition"),
                next_mutation_authorized=True,
            )
        return OneActionOutcome(
            **common,
            state="employer_confirmation_proven",
            mutation_count=1,
            postcondition_sha256=digest("confirmation-postcondition"),
            next_mutation_authorized=False,
            confirmation=EmployerConfirmation(
                provider=request.envelope.provider,
                application_identity_sha256=(
                    request.envelope.application_identity_sha256
                ),
                route_id="hosted_confirmation",
                route_sha256=digest("exact-employer-confirmation-route"),
                anchor_sha256=digest("exact-employer-confirmation-anchor"),
                observation_revisions=(
                    digest("confirmation-observation-1"),
                    digest("confirmation-observation-2"),
                ),
            ),
        )


class TerminalExecutor:
    def __init__(self, stop_code: str) -> None:
        self.stop_code = stop_code
        self.calls = 0

    def __call__(self, request: OneActionRequest) -> OneActionOutcome:
        self.calls += 1
        return OneActionOutcome(
            application_identity_sha256=request.envelope.application_identity_sha256,
            action_id=f"halt-{request.sequence_number}",
            previous_receipt_sha256=request.previous_receipt_sha256,
            receipt_sha256=digest(f"halt-receipt:{request.sequence_number}"),
            state="terminal_halt",
            mutation_count=0,
            postcondition_sha256=None,
            next_mutation_authorized=False,
            stop_code=self.stop_code,
        )


class InvalidExecutor:
    def __call__(self, request: OneActionRequest) -> OneActionOutcome:
        return OneActionOutcome(
            application_identity_sha256=request.envelope.application_identity_sha256,
            action_id="invalid-mutation-count",
            previous_receipt_sha256=request.previous_receipt_sha256,
            receipt_sha256=digest("invalid-executor-receipt"),
            state="action_proven",
            mutation_count=2,
            postcondition_sha256=digest("claimed-postcondition"),
            next_mutation_authorized=True,
        )


class RaisingAfterMutationExecutor:
    def __init__(self) -> None:
        self.calls = 0
        self.mutations = 0

    def __call__(self, request: OneActionRequest) -> OneActionOutcome:
        self.calls += 1
        self.mutations += 1
        raise KeyError(request.sequence_number)


class ObservationExecutor:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, request: OneActionRequest) -> OneActionOutcome:
        self.calls += 1
        return OneActionOutcome(
            application_identity_sha256=request.envelope.application_identity_sha256,
            action_id=f"observe-{request.sequence_number}",
            previous_receipt_sha256=request.previous_receipt_sha256,
            receipt_sha256=digest(f"observe-receipt:{request.sequence_number}"),
            state="observation_proven",
            mutation_count=0,
            postcondition_sha256=digest(
                f"observed-postcondition:{request.sequence_number}"
            ),
            next_mutation_authorized=True,
        )


def prepare_case(
    root: Path, seat: str, **kwargs: Any
) -> tuple[dict[str, object], Path, str]:
    lifecycle_path, lifecycle_sha256, _ = build_lifecycle(root, seat, **kwargs)
    result = prepare_application(
        private_root_value=root,
        lifecycle_path_value=lifecycle_path,
        expected_lifecycle_sha256=lifecycle_sha256,
        seat_id_value=seat,
        correlation_id_value=seat,
    )
    envelope_path = root / "application-envelopes" / seat / f"{seat}.json"
    return result, envelope_path, str(result["envelope_sha256"])


def success_case(root: Path) -> None:
    preparation, envelope_path, envelope_sha256 = prepare_case(root, "success")
    executor = SuccessfulExecutor()
    result = run_application(
        private_root_value=root,
        envelope_path_value=envelope_path,
        expected_envelope_sha256=envelope_sha256,
        executor=executor,
    )
    result_path = root / "application-results" / "success" / "success.json"
    receipt_bytes = result_path.read_bytes()
    receipt = json.loads(receipt_bytes)
    public_bytes = canonical_json_bytes(result)
    if (
        preparation["state"] != "prepared_unclaimed"
        or preparation["evidence_gate_count"] != 6
        or result["state"] != "employer_confirmation_proven"
        or result["ok"] is not True
        or result["one_action_calls"] != 3
        or result["ui_mutations"] != 2
        or result["next_mutation_authorized"] is not False
        or receipt["confirmation_sha256"] != result["confirmation_sha256"]
        or receipt_bytes != canonical_json_bytes(receipt)
        or stat.S_IMODE(result_path.stat().st_mode) != 0o400
        or PRIVATE_SENTINEL.encode("utf-8") in public_bytes
        or PRIVATE_SENTINEL.encode("utf-8") in receipt_bytes
        or len(executor.requests) != 3
    ):
        raise RuntimeError("successful autonomous fixture did not prove its boundary")
    replay_executor = TerminalExecutor("unmapped_ui_or_question")
    try:
        run_application(
            private_root_value=root,
            envelope_path_value=envelope_path,
            expected_envelope_sha256=envelope_sha256,
            executor=replay_executor,
        )
    except ApplicationContractError as exc:
        if exc.failure_code != "policy_or_authority_boundary":
            raise RuntimeError(
                "spent application identity changed failure code"
            ) from exc
    else:
        raise RuntimeError("spent application identity was replayed")
    if replay_executor.calls != 0:
        raise RuntimeError("replay reached the one-action executor")


def terminal_case(root: Path) -> None:
    _, envelope_path, envelope_sha256 = prepare_case(root, "terminal")
    executor = TerminalExecutor("unmapped_ui_or_question")
    result = run_application(
        private_root_value=root,
        envelope_path_value=envelope_path,
        expected_envelope_sha256=envelope_sha256,
        executor=executor,
    )
    if (
        result["ok"] is not False
        or result["state"] != "terminal_halt"
        or result["failure_code"] != "unmapped_ui_or_question"
        or result["one_action_calls"] != 1
        or result["ui_mutations"] != 0
        or executor.calls != 1
    ):
        raise RuntimeError("mapped terminal fixture did not stop exactly")


def invalid_executor_case(root: Path) -> None:
    _, envelope_path, envelope_sha256 = prepare_case(root, "invalid-executor")
    result = run_application(
        private_root_value=root,
        envelope_path_value=envelope_path,
        expected_envelope_sha256=envelope_sha256,
        executor=InvalidExecutor(),
    )
    if (
        result["ok"] is not False
        or result["state"] != "side_effect_uncertain"
        or result["failure_code"] != "side_effect_uncertainty"
        or result["one_action_calls"] != 1
        or result["ui_mutations"] is not None
    ):
        raise RuntimeError("invalid executor result was not contained")


def source_drift_case(root: Path) -> None:
    _, envelope_path, envelope_sha256 = prepare_case(root, "source-drift")
    source = root / "application-evidence" / "source-drift" / "deep_research.json"
    source.chmod(0o600)
    executor = TerminalExecutor("unmapped_ui_or_question")
    result = run_application(
        private_root_value=root,
        envelope_path_value=envelope_path,
        expected_envelope_sha256=envelope_sha256,
        executor=executor,
    )
    if (
        result["state"] != "terminal_halt"
        or result["failure_code"] != "policy_or_authority_boundary"
        or result["one_action_calls"] != 0
        or result["ui_mutations"] != 0
        or executor.calls != 0
    ):
        raise RuntimeError("source drift reached the one-action executor")


def executor_exception_case(root: Path) -> None:
    _, envelope_path, envelope_sha256 = prepare_case(root, "executor-exception")
    executor = RaisingAfterMutationExecutor()
    result = run_application(
        private_root_value=root,
        envelope_path_value=envelope_path,
        expected_envelope_sha256=envelope_sha256,
        executor=executor,
    )
    result_path = (
        root / "application-results" / "executor-exception" / "executor-exception.json"
    )
    receipt_bytes = result_path.read_bytes()
    receipt = json.loads(receipt_bytes)
    if (
        result["state"] != "side_effect_uncertain"
        or result["failure_code"] != "side_effect_uncertainty"
        or result["one_action_calls"] != 1
        or result["ui_mutations"] is not None
        or result["next_mutation_authorized"] is not False
        or receipt["next_mutation_authorized"] is not False
        or receipt_bytes != canonical_json_bytes(receipt)
        or stat.S_IMODE(result_path.stat().st_mode) != 0o400
        or executor.calls != 1
        or executor.mutations != 1
    ):
        raise RuntimeError("post-mutation executor exception was not contained")
    replay_executor = RaisingAfterMutationExecutor()
    try:
        run_application(
            private_root_value=root,
            envelope_path_value=envelope_path,
            expected_envelope_sha256=envelope_sha256,
            executor=replay_executor,
        )
    except ApplicationContractError as exc:
        if exc.failure_code != "policy_or_authority_boundary":
            raise RuntimeError("uncertain identity changed replay code") from exc
    else:
        raise RuntimeError("uncertain application identity was replayed")
    if replay_executor.calls != 0 or replay_executor.mutations != 0:
        raise RuntimeError("uncertain application identity reached a second call")


def action_budget_case(root: Path) -> None:
    _, envelope_path, envelope_sha256 = prepare_case(
        root, "action-budget", maximum_one_action_calls=2
    )
    executor = ObservationExecutor()
    result = run_application(
        private_root_value=root,
        envelope_path_value=envelope_path,
        expected_envelope_sha256=envelope_sha256,
        executor=executor,
    )
    if (
        result["state"] != "terminal_halt"
        or result["failure_code"] != "policy_or_authority_boundary"
        or result["one_action_calls"] != 2
        or result["ui_mutations"] != 0
        or executor.calls != 2
    ):
        raise RuntimeError("bounded action authority did not stop exactly")


def preparation_refusal_cases(root: Path) -> None:
    for seat, kwargs in (
        ("bad-gate", {"bad_stage": "deep_research"}),
        ("extra-review-field", {"extra_field": ("human_review_required", True)}),
    ):
        lifecycle_path, lifecycle_sha256, _ = build_lifecycle(root, seat, **kwargs)
        try:
            prepare_application(
                private_root_value=root,
                lifecycle_path_value=lifecycle_path,
                expected_lifecycle_sha256=lifecycle_sha256,
                seat_id_value=seat,
                correlation_id_value=seat,
            )
        except ApplicationContractError:
            pass
        else:
            raise RuntimeError(f"{seat} preparation did not refuse")
        refusal_path = root / "application-preparation-refusals" / seat / f"{seat}.json"
        refusal_bytes = refusal_path.read_bytes()
        refusal = json.loads(refusal_bytes)
        if (
            refusal["state"] != "preparation_refused"
            or refusal_bytes != canonical_json_bytes(refusal)
            or stat.S_IMODE(refusal_path.stat().st_mode) != 0o400
        ):
            raise RuntimeError(f"{seat} lacks immutable refusal evidence")


def cli_case(root: Path) -> None:
    lifecycle_path, lifecycle_sha256, _ = build_lifecycle(root, "cli")
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(SOURCE_ROOT)
    completed = subprocess.run(
        [
            sys.executable,
            "-P",
            "-m",
            "taey_apply.application_prepare_cli",
            "--private-root",
            str(root),
            "--lifecycle-file",
            str(lifecycle_path),
            "--lifecycle-sha256",
            lifecycle_sha256,
            "--seat-id",
            "cli",
            "--correlation-id",
            "cli",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    result = json.loads(completed.stdout)
    if (
        completed.stderr
        or result["state"] != "prepared_unclaimed"
        or PRIVATE_SENTINEL in completed.stdout
    ):
        raise RuntimeError("application preparation CLI boundary failed")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="taey-apply-autonomous-") as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        success_case(root)
        terminal_case(root)
        invalid_executor_case(root)
        source_drift_case(root)
        executor_exception_case(root)
        action_budget_case(root)
        preparation_refusal_cases(root)
        cli_case(root)
    result = {
        "schema": "taey_apply_application_boundary_validation_v1",
        "verdict": "PASS",
        "production_mutations": 0,
        "fixture_cases": 9,
        "validated": [
            "six autonomous prerequisite gates",
            "single-use frozen envelope",
            "one-action receipt chaining",
            "exact employer confirmation",
            "mapped first-error terminalization",
            "invalid executor side-effect containment",
            "source drift before executor mutation",
            "post-mutation executor exception containment",
            "bounded action authority",
            "no routine human review field",
            "private-value omission",
        ],
    }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
