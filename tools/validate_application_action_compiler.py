#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import stat
import sys
import tempfile
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "tools"))

from taey_apply.application_action_compiler import (  # noqa: E402
    ApplicationActionCompilerError,
    DECISION_SCHEMA,
    GreenhouseActionCompiler,
    REQUIRED_HANDS_COMMIT,
)
from taey_apply.application_contract import (  # noqa: E402
    OneActionRequest,
    load_application_envelope,
)
from taey_apply.application_materializer import materialize_application_context  # noqa: E402
from taey_apply.application_preparer import prepare_application  # noqa: E402
from taey_apply.contract import canonical_json_bytes  # noqa: E402
from validate_application_materializer import (  # noqa: E402
    PRIVATE_SENTINEL,
    fixture,
)


def digest(value: str | bytes) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def ref(number: int) -> str:
    return f"r_{number:032x}"


def native_ref(number: int) -> str:
    return f"nd1_{number:064x}"


def decision(
    action: str,
    surface_revision: str,
    control_ref: str | None,
    *,
    fact_key: str | None = None,
    option_name: str | None = None,
    work_keys: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema": DECISION_SCHEMA,
        "action": action,
        "ref": control_ref,
        "revision": surface_revision,
        "fact_key": fact_key,
        "work_evidence_keys": work_keys or [],
        "expected_option_name": option_name,
        "stop_code": None,
    }


def form_capsule(
    identity: str,
    revision: str,
    controls: list[dict[str, Any]],
    *,
    required_complete: bool | None = None,
) -> dict[str, Any]:
    capsule: dict[str, Any] = {
        "schema": "ats_greenhouse_next_action_surface_v1",
        "provider": "greenhouse",
        "application_identity_sha256": identity,
        "surface": "form",
        "revision": revision,
        "source_surface_sha256": digest(f"surface:{revision}"),
        "controls": controls,
        "route_grammar": "greenhouse_hosted_application",
        "complete_form_sha256": digest(f"complete:{revision}"),
    }
    if required_complete is not None:
        capsule["required_controls_complete"] = required_complete
    return capsule


def options_capsule(
    identity: str,
    revision: str,
    combo_ref: str,
    option_ref: str,
) -> dict[str, Any]:
    return {
        "schema": "ats_greenhouse_next_action_surface_v1",
        "provider": "greenhouse",
        "application_identity_sha256": identity,
        "surface": "options",
        "revision": revision,
        "source_surface_sha256": digest(f"surface:{revision}"),
        "controls": [
            {
                "ref": option_ref,
                "name": "United States",
                "role": "menu item",
                "operations": ["select_option"],
            }
        ],
        "origin": {
            "combo_ref": combo_ref,
            "name": "Country",
            "role": "combo box",
            "form_revision": digest("form-before-options"),
            "match_count": 1,
        },
    }


def native_capsule(
    identity: str,
    revision: str,
    key: str,
    control_ref: str,
) -> dict[str, Any]:
    return {
        "schema": "ats_greenhouse_next_action_surface_v1",
        "provider": "greenhouse",
        "application_identity_sha256": identity,
        "surface": "native_dialog",
        "revision": revision,
        "source_surface_sha256": digest(f"surface:{revision}"),
        "mapped": {
            key: [
                {
                    "key": key,
                    "ref": control_ref,
                    "role": "entry" if key == "location_entry" else "push button",
                    "states": ["enabled", "showing", "visible"],
                }
            ]
        },
    }


def frozen_action(root: Path, seat: str, correlation: str) -> dict[str, Any]:
    path = root / "actions" / seat / f"{correlation}.json"
    if stat.S_IMODE(path.stat().st_mode) != 0o400:
        raise RuntimeError("compiled action mode drifted")
    return json.loads(path.read_bytes())


def manifest(root: Path, seat: str, correlation: str) -> dict[str, Any]:
    path = root / "transactions" / seat / f"{correlation}.json"
    if stat.S_IMODE(path.stat().st_mode) != 0o400:
        raise RuntimeError("Presence manifest mode drifted")
    return json.loads(path.read_bytes())


def compile_step(
    compiler: GreenhouseActionCompiler,
    envelope: Any,
    envelope_sha256: str,
    sequence: int,
    capsule: object | None,
    exact_decision: object | None,
) -> tuple[Any, dict[str, Any]]:
    previous = None if sequence == 1 else digest(f"receipt:{sequence - 1}")
    correlation = f"turn-{sequence:02d}"
    compiled = compiler.compile(
        OneActionRequest(
            envelope=envelope,
            envelope_sha256=envelope_sha256,
            sequence_number=sequence,
            previous_receipt_sha256=previous,
        ),
        event_id_value=f"event-{sequence:02d}",
        correlation_id_value=correlation,
        surface_capsule=capsule,
        decision=exact_decision,
    )
    return compiled, frozen_action(PRIVATE_ROOT, "runtime", correlation)


def expect_failure(
    compiler: GreenhouseActionCompiler,
    envelope: Any,
    envelope_sha256: str,
    sequence: int,
    capsule: object,
    exact_decision: object,
    expected: str,
) -> None:
    try:
        compiler.compile(
            OneActionRequest(
                envelope=envelope,
                envelope_sha256=envelope_sha256,
                sequence_number=sequence,
                previous_receipt_sha256=digest(f"receipt:{sequence - 1}"),
            ),
            event_id_value=f"failure-{sequence}",
            correlation_id_value=f"failure-{sequence}",
            surface_capsule=capsule,
            decision=exact_decision,
        )
    except ApplicationActionCompilerError as exc:
        if exc.failure_code != expected:
            raise RuntimeError(
                f"compiler failure changed: {exc.failure_code} != {expected}"
            ) from exc
    else:
        raise RuntimeError("compiler accepted a terminal decision boundary")


def success_case(root: Path) -> None:
    def add_country(
        manifest_value: dict[str, Any], sources: dict[str, dict[str, Any]]
    ) -> None:
        sources["facts"]["country"] = {
            "value": "United States",
            "evidence_sha256": digest("runtime:country"),
        }
        manifest_value["applicant_facts"] = sources["facts"]

    source_path, source_sha256, identity = fixture(root, "runtime", add_country)
    materialized = materialize_application_context(
        private_root_value=root,
        manifest_path_value=source_path,
        expected_manifest_sha256=source_sha256,
        seat_id_value="lifecycle",
        correlation_id_value="lifecycle",
    )
    lifecycle_path = (
        root / "application-materializations" / "lifecycle" / "lifecycle.lifecycle.json"
    )
    prepared = prepare_application(
        private_root_value=root,
        lifecycle_path_value=lifecycle_path,
        expected_lifecycle_sha256=materialized["lifecycle_sha256"],
        seat_id_value="lifecycle",
        correlation_id_value="lifecycle",
    )
    envelope_path = root / "application-envelopes" / "lifecycle" / "lifecycle.json"
    envelope, envelope_sha256 = load_application_envelope(
        root, envelope_path, prepared["envelope_sha256"]
    )
    compiler = GreenhouseActionCompiler(
        private_root_value=root,
        seat_id_value="runtime",
        display_value=":17",
        hands_commit_value=REQUIRED_HANDS_COMMIT,
    )

    compiled, action = compile_step(compiler, envelope, envelope_sha256, 1, None, None)
    if (
        action["action"] != {"kind": "observe_form"}
        or action["expected_prior_event_hash"] is not None
        or manifest(root, "runtime", "turn-01")["event_id"] != "event-01"
        or compiled.action_kind != "observe_form"
    ):
        raise RuntimeError("initial observe action was not exact")

    full_name_ref = ref(1)
    focus_revision = digest("focus-revision")
    focus_surface = form_capsule(
        identity,
        focus_revision,
        [
            {
                "ref": full_name_ref,
                "name": "Full name",
                "role": "entry",
                "operations": ["focus"],
                "is_empty": True,
            }
        ],
    )
    _, action = compile_step(
        compiler,
        envelope,
        envelope_sha256,
        2,
        focus_surface,
        decision("focus", focus_revision, full_name_ref, fact_key="full_name"),
    )
    if action["action"] != {
        "kind": "focus",
        "ref": full_name_ref,
        "revision": focus_revision,
    }:
        raise RuntimeError("focus action drifted")

    fill_revision = digest("fill-revision")
    fill_surface = form_capsule(
        identity,
        fill_revision,
        [
            {
                "ref": full_name_ref,
                "name": "Full name",
                "role": "entry",
                "operations": ["focus", "fill"],
                "is_empty": True,
            }
        ],
    )
    compiled, action = compile_step(
        compiler,
        envelope,
        envelope_sha256,
        3,
        fill_surface,
        decision(
            "fill",
            fill_revision,
            full_name_ref,
            fact_key="full_name",
            work_keys=["automation"],
        ),
    )
    public_compiled = canonical_json_bytes(asdict(compiled))
    if (
        action["action"]["value"] != PRIVATE_SENTINEL
        or action["action"]["value_sha256"] != digest(PRIVATE_SENTINEL)
        or PRIVATE_SENTINEL.encode() in public_compiled
        or "application_context" in action["action"]
    ):
        raise RuntimeError("private fact was not copied only into the frozen action")

    combo_ref = ref(2)
    combo_revision = digest("combo-revision")
    combo_surface = form_capsule(
        identity,
        combo_revision,
        [
            {
                "ref": combo_ref,
                "name": "Country",
                "role": "combo box",
                "operations": ["open_combo"],
                "has_semantic_value": False,
                "combo_safety": {
                    "geometry": "contained_by_active_document",
                    "refusal": None,
                    "scroll_frontier": False,
                },
            }
        ],
    )
    compile_step(
        compiler,
        envelope,
        envelope_sha256,
        4,
        combo_surface,
        decision("open_combo", combo_revision, combo_ref, fact_key="country"),
    )
    option_ref = ref(3)
    option_revision = digest("option-revision")
    _, action = compile_step(
        compiler,
        envelope,
        envelope_sha256,
        5,
        options_capsule(identity, option_revision, combo_ref, option_ref),
        decision(
            "select_option",
            option_revision,
            None,
            fact_key="country",
        ),
    )
    if action["action"]["expected_option_name"] != "United States":
        raise RuntimeError("private exact option was not resolved")

    upload_ref = ref(4)
    upload_revision = digest("upload-revision")
    upload_surface = form_capsule(
        identity,
        upload_revision,
        [
            {
                "ref": upload_ref,
                "name": "Resume",
                "role": "push button",
                "operations": ["open_upload"],
                "artifact_slot": "resume",
            }
        ],
    )
    compile_step(
        compiler,
        envelope,
        envelope_sha256,
        6,
        upload_surface,
        decision("open_upload", upload_revision, upload_ref),
    )
    native_steps = (
        (7, "chooser_location", "chooser_widget", native_ref(5)),
        (8, "chooser_select_all", "location_entry", native_ref(6)),
        (9, "chooser_type_path", "location_entry", native_ref(7)),
        (10, "chooser_confirm", "open_button", native_ref(8)),
    )
    for sequence, kind, key, control_ref in native_steps:
        revision = digest(f"native-revision:{sequence}")
        _, action = compile_step(
            compiler,
            envelope,
            envelope_sha256,
            sequence,
            native_capsule(identity, revision, key, control_ref),
            decision(kind, revision, control_ref),
        )
        if action["action"]["kind"] != kind:
            raise RuntimeError("native chooser action order drifted")
        if kind in {"chooser_type_path", "chooser_confirm"}:
            artifact = action["action"]["artifact"]
            if (
                artifact["slot"] != "resume"
                or not Path(artifact["path"]).is_absolute()
                or artifact["name"] != Path(artifact["path"]).name
            ):
                raise RuntimeError("private artifact was not bound exactly")

    submit_ref = ref(9)
    submit_revision = digest("submit-revision")
    incomplete = form_capsule(
        identity,
        submit_revision,
        [
            {
                "ref": submit_ref,
                "name": "Submit Application",
                "role": "push button",
                "operations": ["submit"],
                "boundary": "submit",
            }
        ],
    )
    submit_decision = decision("submit", submit_revision, submit_ref)
    expect_failure(
        compiler,
        envelope,
        envelope_sha256,
        11,
        incomplete,
        submit_decision,
        "exact_postcondition_failure",
    )
    if (root / "actions" / "runtime" / "failure-11.json").exists():
        raise RuntimeError("submit without required-control proof wrote an action")
    complete = form_capsule(
        identity,
        submit_revision,
        incomplete["controls"],
        required_complete=True,
    )
    _, action = compile_step(
        compiler,
        envelope,
        envelope_sha256,
        11,
        complete,
        submit_decision,
    )
    precondition = action["action"]["precondition"]
    if (
        precondition["required_controls_complete"] is not True
        or precondition["truth_attested"] is not True
        or [item["slot"] for item in precondition["artifacts"]] != ["resume"]
    ):
        raise RuntimeError("submit precondition was not compiled from exact evidence")


def refusal_cases(root: Path) -> int:
    def add_country(
        manifest_value: dict[str, Any], sources: dict[str, dict[str, Any]]
    ) -> None:
        sources["facts"]["country"] = {
            "value": "United States",
            "evidence_sha256": digest("refusal:country"),
        }
        manifest_value["applicant_facts"] = sources["facts"]

    source_path, source_sha256, identity = fixture(root, "refusal", add_country)
    materialized = materialize_application_context(
        private_root_value=root,
        manifest_path_value=source_path,
        expected_manifest_sha256=source_sha256,
        seat_id_value="refusal-life",
        correlation_id_value="refusal-life",
    )
    lifecycle_path = (
        root
        / "application-materializations"
        / "refusal-life"
        / "refusal-life.lifecycle.json"
    )
    prepared = prepare_application(
        private_root_value=root,
        lifecycle_path_value=lifecycle_path,
        expected_lifecycle_sha256=materialized["lifecycle_sha256"],
        seat_id_value="refusal-life",
        correlation_id_value="refusal-life",
    )
    envelope, envelope_sha256 = load_application_envelope(
        root,
        root / "application-envelopes" / "refusal-life" / "refusal-life.json",
        prepared["envelope_sha256"],
    )

    def ready_compiler(seat: str) -> GreenhouseActionCompiler:
        instance = GreenhouseActionCompiler(
            private_root_value=root,
            seat_id_value=seat,
            display_value=":17",
            hands_commit_value=REQUIRED_HANDS_COMMIT,
        )
        instance.compile(
            OneActionRequest(envelope, envelope_sha256, 1, None),
            event_id_value=f"{seat}-initial",
            correlation_id_value=f"{seat}-initial",
            surface_capsule=None,
            decision=None,
        )
        return instance

    def ready_options_compiler(seat: str) -> GreenhouseActionCompiler:
        instance = ready_compiler(seat)
        combo_revision = digest(f"{seat}:combo")
        combo_ref = ref(30)
        instance.compile(
            OneActionRequest(
                envelope,
                envelope_sha256,
                2,
                digest("receipt:1"),
            ),
            event_id_value=f"{seat}-open-event",
            correlation_id_value=f"{seat}-open",
            surface_capsule=form_capsule(
                identity,
                combo_revision,
                [
                    {
                        "ref": combo_ref,
                        "name": "Country",
                        "role": "combo box",
                        "operations": ["open_combo"],
                        "has_semantic_value": False,
                        "combo_safety": {
                            "geometry": "contained_by_active_document",
                            "refusal": None,
                            "scroll_frontier": False,
                        },
                    }
                ],
            ),
            decision=decision(
                "open_combo",
                combo_revision,
                combo_ref,
                fact_key="country",
            ),
        )
        return instance

    failures = 0
    control_ref = ref(20)
    revision = digest("refusal-revision")
    surface = form_capsule(
        identity,
        revision,
        [
            {
                "ref": control_ref,
                "name": "Unknown employer question",
                "role": "entry",
                "operations": ["focus"],
                "is_empty": True,
            }
        ],
    )
    for seat, exact_decision, expected in (
        (
            "missing-fact",
            decision("focus", revision, control_ref, fact_key="not_known"),
            "missing_truthful_applicant_data",
        ),
        (
            "stale",
            decision("focus", digest("stale"), control_ref, fact_key="full_name"),
            "exact_postcondition_failure",
        ),
        (
            "unmapped",
            {
                "schema": DECISION_SCHEMA,
                "action": "halt",
                "ref": None,
                "revision": None,
                "fact_key": None,
                "work_evidence_keys": [],
                "expected_option_name": None,
                "stop_code": "unmapped_ui_or_question",
            },
            "unmapped_ui_or_question",
        ),
    ):
        instance = ready_compiler(seat)
        expect_failure(
            instance,
            envelope,
            envelope_sha256,
            2,
            surface,
            exact_decision,
            expected,
        )
        failures += 1
    review = decision("focus", revision, control_ref, fact_key="full_name")
    review["human_review_required"] = True
    instance = ready_compiler("review-field")
    expect_failure(
        instance,
        envelope,
        envelope_sha256,
        2,
        surface,
        review,
        "policy_or_authority_boundary",
    )
    failures += 1

    option_revision = digest("refusal-options")
    zero_match = options_capsule(identity, option_revision, ref(30), ref(31))
    zero_match["controls"][0]["name"] = "Canada"
    duplicate_match = options_capsule(identity, option_revision, ref(30), ref(32))
    duplicate_match["controls"].append(
        {
            "ref": ref(33),
            "name": "United States",
            "role": "menu item",
            "operations": ["select_option"],
        }
    )
    for seat, options in (
        ("option-zero-match", zero_match),
        ("option-duplicate-match", duplicate_match),
    ):
        expect_failure(
            ready_options_compiler(seat),
            envelope,
            envelope_sha256,
            3,
            options,
            decision(
                "select_option",
                option_revision,
                None,
                fact_key="country",
            ),
            "unmapped_ui_or_question",
        )
        failures += 1
    return failures


def static_boundary() -> None:
    source = (
        REPO_ROOT / "src" / "taey_apply" / "application_action_compiler.py"
    ).read_text(encoding="utf-8")
    forbidden = (
        "subprocess",
        "urllib",
        "httpx",
        "requests",
        "atspi",
        "taeys_hands",
        "human_review",
        "approval_required",
        "review_queue",
    )
    leaked = [token for token in forbidden if token in source]
    if leaked:
        raise RuntimeError(f"compiler crossed its public boundary: {leaked}")


if __name__ == "__main__":
    static_boundary()
    with tempfile.TemporaryDirectory(prefix="taey-apply-action-compiler-") as temp:
        PRIVATE_ROOT = Path(temp)
        PRIVATE_ROOT.chmod(0o700)
        success_case(PRIVATE_ROOT)
    with tempfile.TemporaryDirectory(prefix="taey-apply-action-refusals-") as temp:
        PRIVATE_ROOT = Path(temp)
        PRIVATE_ROOT.chmod(0o700)
        failures = refusal_cases(PRIVATE_ROOT)
    print(
        json.dumps(
            {
                "fixture_cases": 1 + failures,
                "frozen_action_kinds": 11,
                "human_review_states": 0,
                "network_calls": 0,
                "production_mutations": 0,
                "status": "PASS",
                "submit_requires_upstream_required_controls_complete": True,
            },
            sort_keys=True,
        )
    )
