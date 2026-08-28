from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping
import uuid

from .application_contract import (
    ApplicationContractError,
    OneActionRequest,
    validate_application_envelope_sources,
)
from .contract import (
    IntakeContractError,
    _read_owned_file,
    canonical_json_bytes,
    read_private_json,
    resolve_private_reference,
    sha256_hex,
    validate_digest,
    validate_git_commit,
    validate_private_root,
    validate_public_id,
)
from .preparer import (
    _directory_metadata,
    _ensure_bucket,
    _fsync_directory,
    _path_exists,
    _write_frozen_bytes,
)


DECISION_SCHEMA = "taey_apply_greenhouse_action_decision_v1"
FROZEN_ACTION_SCHEMA = "ats_greenhouse_frozen_action_v1"
PRESENCE_MANIFEST_SCHEMA = "taey_greenhouse_ats_private_manifest_v1"
SURFACE_SCHEMA = "ats_greenhouse_next_action_surface_v1"
REQUIRED_HANDS_COMMIT = "3218faae41aad580da82cd396808ac72e118174e"

_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
_DISPLAY_RE = re.compile(r":[1-9][0-9]{0,2}")
_REF_RE = re.compile(r"(?:r_[0-9a-f]{32}|nd1_[0-9a-f]{64})")
_TOKEN_RE = re.compile(r"[a-z][a-z0-9_]{0,127}")
_SEMANTIC_TOKEN_RE = re.compile(r"\S(?:.*\S)?", flags=re.ASCII)
_DECISION_KEYS = frozenset(
    {
        "schema",
        "action",
        "ref",
        "revision",
        "fact_key",
        "work_evidence_keys",
        "expected_option_name",
        "stop_code",
    }
)
_DECISION_ACTIONS = frozenset(
    {
        "focus",
        "fill",
        "scroll_combo",
        "open_combo",
        "select_option",
        "activate_choice",
        "open_upload",
        "chooser_location",
        "chooser_select_all",
        "chooser_type_path",
        "chooser_confirm",
        "submit",
        "halt",
    }
)
_STOP_CODES = frozenset(
    {
        "exact_postcondition_failure",
        "unmapped_ui_or_question",
        "missing_truthful_applicant_data",
        "policy_or_authority_boundary",
        "side_effect_uncertainty",
    }
)
_CONTEXT_KEYS = frozenset(
    {
        "schema",
        "provider",
        "application_identity_sha256",
        "source_manifest_sha256",
        "required_fact_keys",
        "stages",
        "applicant_facts",
        "work_evidence",
        "submission_policy",
        "truth_attestation_sha256",
    }
)
_CAPSULE_COMMON_KEYS = frozenset(
    {
        "schema",
        "provider",
        "application_identity_sha256",
        "surface",
        "revision",
        "source_surface_sha256",
    }
)
_CONTROL_KEYS = frozenset(
    {
        "ref",
        "name",
        "role",
        "operations",
        "is_empty",
        "has_semantic_value",
        "artifact_slot",
        "boundary",
        "combo_safety",
        "semantic_token",
    }
)
_FORM_OPERATIONS = frozenset(
    {
        "focus",
        "fill",
        "scroll_combo",
        "open_combo",
        "activate_choice",
        "open_upload",
        "submit",
    }
)
_NATIVE_SEQUENCE = {
    "open_upload": ("chooser_location", "chooser_widget"),
    "chooser_location": ("chooser_select_all", "location_entry"),
    "chooser_select_all": ("chooser_type_path", "location_entry"),
    "chooser_type_path": ("chooser_confirm", "open_button"),
}


class ApplicationActionCompilerError(RuntimeError):
    def __init__(self, failure_code: str, message: str) -> None:
        super().__init__(message)
        if failure_code not in _STOP_CODES:
            raise ValueError("unsupported application action compiler failure")
        self.failure_code = failure_code


@dataclass(frozen=True, slots=True)
class CompiledGreenhouseAction:
    seat_id: str
    event_id: str
    correlation_id: str
    display: str
    transaction_id: str
    action_id: str
    action_kind: str
    decision_sha256: str
    surface_capsule_sha256: str | None
    frozen_action_sha256: str
    presence_manifest_sha256: str


@dataclass(frozen=True, slots=True)
class GreenhouseDecisionContext:
    application_identity_sha256: str
    surface_capsule: Mapping[str, Any]
    available_fact_keys: tuple[str, ...]
    available_work_evidence_keys: tuple[str, ...]
    previous_action_kind: str


def _compiler_error(exc: BaseException) -> ApplicationActionCompilerError:
    if isinstance(exc, ApplicationActionCompilerError):
        return exc
    if isinstance(exc, IntakeContractError) and exc.failure_code == (
        "preparation_write_indeterminate"
    ):
        return ApplicationActionCompilerError(
            "side_effect_uncertainty", "application action write became uncertain"
        )
    if isinstance(exc, (ApplicationContractError, IntakeContractError)):
        return ApplicationActionCompilerError(
            "policy_or_authority_boundary", "application action authority is invalid"
        )
    return ApplicationActionCompilerError(
        "side_effect_uncertainty", "application action compilation became uncertain"
    )


def _exact_mapping(value: object, expected: frozenset[str], context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ApplicationActionCompilerError(
            "policy_or_authority_boundary", f"{context} must be an object"
        )
    if frozenset(value) != expected:
        raise ApplicationActionCompilerError(
            "policy_or_authority_boundary", f"{context} fields are not exact"
        )
    return value


def _digest(value: object, context: str) -> str:
    try:
        return validate_digest(value, context)
    except IntakeContractError as exc:
        raise ApplicationActionCompilerError(
            "exact_postcondition_failure", f"{context} is not an exact digest"
        ) from exc


def _token(value: object, context: str) -> str:
    if not isinstance(value, str) or _TOKEN_RE.fullmatch(value) is None:
        raise ApplicationActionCompilerError(
            "unmapped_ui_or_question", f"{context} is not an exact key"
        )
    return value


def _validated_decision(value: object) -> dict[str, Any]:
    decision = _exact_mapping(value, _DECISION_KEYS, "action decision")
    if decision["schema"] != DECISION_SCHEMA or decision["action"] not in _DECISION_ACTIONS:
        raise ApplicationActionCompilerError(
            "unmapped_ui_or_question", "action decision is unsupported"
        )
    action = str(decision["action"])
    ref = decision["ref"]
    revision = decision["revision"]
    fact_key = decision["fact_key"]
    option_name = decision["expected_option_name"]
    stop_code = decision["stop_code"]
    raw_work_keys = decision["work_evidence_keys"]
    if not isinstance(raw_work_keys, list) or len(raw_work_keys) != len(set(raw_work_keys)):
        raise ApplicationActionCompilerError(
            "unmapped_ui_or_question", "work evidence keys are not exact"
        )
    work_keys = [_token(item, "work evidence key") for item in raw_work_keys]
    if action == "halt":
        if (
            ref is not None
            or revision is not None
            or fact_key is not None
            or option_name is not None
            or stop_code not in _STOP_CODES
        ):
            raise ApplicationActionCompilerError(
                "unmapped_ui_or_question", "terminal decision is malformed"
            )
        raise ApplicationActionCompilerError(str(stop_code), "Taey returned a terminal decision")
    if stop_code is not None:
        raise ApplicationActionCompilerError(
            "unmapped_ui_or_question", "nonterminal decision carries a stop code"
        )
    if action == "select_option":
        if ref is not None:
            raise ApplicationActionCompilerError(
                "unmapped_ui_or_question",
                "private option resolution does not accept a model-selected ref",
            )
    elif not isinstance(ref, str) or _REF_RE.fullmatch(ref) is None:
        raise ApplicationActionCompilerError(
            "unmapped_ui_or_question", "decision ref is not current and exact"
        )
    revision = _digest(revision, "decision revision")
    fact_actions = {
        "focus",
        "fill",
        "scroll_combo",
        "open_combo",
        "select_option",
        "activate_choice",
    }
    if action in fact_actions:
        fact_key = _token(fact_key, "fact key")
    elif fact_key is not None:
        raise ApplicationActionCompilerError(
            "unmapped_ui_or_question", "decision fact key exceeds action authority"
        )
    if action == "select_option":
        if option_name is not None:
            raise ApplicationActionCompilerError(
                "unmapped_ui_or_question",
                "private option resolution does not accept a model-selected name",
            )
    elif option_name is not None:
        raise ApplicationActionCompilerError(
            "unmapped_ui_or_question", "option name exceeds action authority"
        )
    return {
        "schema": DECISION_SCHEMA,
        "action": action,
        "ref": ref,
        "revision": revision,
        "fact_key": fact_key,
        "work_evidence_keys": work_keys,
        "expected_option_name": option_name,
        "stop_code": None,
    }


def _validated_context(private_root: Path, request: OneActionRequest) -> dict[str, Any]:
    try:
        path = resolve_private_reference(
            private_root,
            request.envelope.application_context_ref,
            "application context",
        )
        context, raw = read_private_json(path, "application context")
    except IntakeContractError as exc:
        raise ApplicationActionCompilerError(
            "policy_or_authority_boundary", "application context is unavailable"
        ) from exc
    if sha256_hex(raw) != request.envelope.application_context_sha256:
        raise ApplicationActionCompilerError(
            "policy_or_authority_boundary", "application context digest differs"
        )
    context = dict(_exact_mapping(context, _CONTEXT_KEYS, "application context"))
    if (
        context["schema"] != "taey_apply_application_context_v1"
        or context["provider"] != "greenhouse"
        or context["application_identity_sha256"]
        != request.envelope.application_identity_sha256
    ):
        raise ApplicationActionCompilerError(
            "policy_or_authority_boundary", "application context identity differs"
        )
    _digest(context["source_manifest_sha256"], "source manifest")
    required = context["required_fact_keys"]
    facts = context["applicant_facts"]
    work_evidence = context["work_evidence"]
    submission_policy = context["submission_policy"]
    if (
        not isinstance(required, list)
        or not required
        or len(required) != len(set(required))
        or not isinstance(facts, Mapping)
        or not isinstance(work_evidence, Mapping)
        or not isinstance(submission_policy, Mapping)
    ):
        raise ApplicationActionCompilerError(
            "policy_or_authority_boundary", "application context authority is malformed"
        )
    required = [_token(item, "required fact key") for item in required]
    for key in required:
        _fact(context, key)
    attestation = sha256_hex(
        canonical_json_bytes(
            {
                "application_identity_sha256": context[
                    "application_identity_sha256"
                ],
                "applicant_facts_sha256": sha256_hex(canonical_json_bytes(facts)),
                "required_fact_keys": required,
                "submission_policy_sha256": sha256_hex(
                    canonical_json_bytes(submission_policy)
                ),
                "work_evidence_sha256": sha256_hex(
                    canonical_json_bytes(work_evidence)
                ),
            }
        )
    )
    if _digest(context["truth_attestation_sha256"], "truth attestation") != attestation:
        raise ApplicationActionCompilerError(
            "policy_or_authority_boundary", "truth attestation differs"
        )
    return context


def _validated_operations(value: object) -> list[str]:
    if (
        not isinstance(value, list)
        or len(value) != len(set(value))
        or any(item not in _FORM_OPERATIONS | {"select_option"} for item in value)
    ):
        raise ApplicationActionCompilerError(
            "exact_postcondition_failure", "surface operations are not exact"
        )
    return list(value)


def _validated_control(value: object, *, options: bool) -> dict[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ApplicationActionCompilerError(
            "exact_postcondition_failure", "surface control is not an object"
        )
    if not frozenset(value) <= _CONTROL_KEYS or not {
        "ref",
        "name",
        "role",
        "operations",
    } <= set(value):
        raise ApplicationActionCompilerError(
            "exact_postcondition_failure", "surface control fields are invalid"
        )
    ref = value["ref"]
    name = value["name"]
    role = value["role"]
    if (
        not isinstance(ref, str)
        or _REF_RE.fullmatch(ref) is None
        or not isinstance(name, str)
        or not isinstance(role, str)
        or not role
    ):
        raise ApplicationActionCompilerError(
            "exact_postcondition_failure", "surface control identity is invalid"
        )
    operations = _validated_operations(value["operations"])
    if options and operations != ["select_option"]:
        raise ApplicationActionCompilerError(
            "exact_postcondition_failure", "fresh option operation is invalid"
        )
    if "semantic_token" in value:
        semantic_token = value["semantic_token"]
        if (
            not options
            or not isinstance(semantic_token, str)
            or _SEMANTIC_TOKEN_RE.fullmatch(semantic_token) is None
        ):
            raise ApplicationActionCompilerError(
                "exact_postcondition_failure", "surface semantic token is invalid"
            )
    result = dict(value)
    result["operations"] = operations
    for key in ("is_empty", "has_semantic_value"):
        if key in result and not isinstance(result[key], bool):
            raise ApplicationActionCompilerError(
                "exact_postcondition_failure", f"surface {key} is invalid"
            )
    if "artifact_slot" in result and result["artifact_slot"] not in {"resume", "cover"}:
        raise ApplicationActionCompilerError(
            "exact_postcondition_failure", "surface artifact slot is unsupported"
        )
    if "boundary" in result and result["boundary"] != "submit":
        raise ApplicationActionCompilerError(
            "exact_postcondition_failure", "surface boundary is unsupported"
        )
    if "combo_safety" in result:
        safety = _exact_mapping(
            result["combo_safety"],
            frozenset({"geometry", "refusal", "scroll_frontier"}),
            "combo safety",
        )
        if (
            safety["geometry"]
            not in {"contained_by_active_document", "refused"}
            or (
                safety["refusal"] is not None
                and not isinstance(safety["refusal"], str)
            )
            or not isinstance(safety["scroll_frontier"], bool)
        ):
            raise ApplicationActionCompilerError(
                "exact_postcondition_failure", "combo safety is invalid"
            )
        result["combo_safety"] = dict(safety)
    return result


def _validated_surface_capsule(
    value: object,
    request: OneActionRequest,
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ApplicationActionCompilerError(
            "exact_postcondition_failure", "surface capsule is not an object"
        )
    capsule = dict(value)
    if (
        capsule.get("schema") != SURFACE_SCHEMA
        or capsule.get("provider") != "greenhouse"
        or capsule.get("application_identity_sha256")
        != request.envelope.application_identity_sha256
        or capsule.get("surface") not in {"form", "options", "native_dialog"}
    ):
        raise ApplicationActionCompilerError(
            "exact_postcondition_failure", "surface capsule identity differs"
        )
    _digest(capsule.get("revision"), "surface revision")
    _digest(capsule.get("source_surface_sha256"), "source surface")
    surface = capsule["surface"]
    if surface == "form":
        required = _CAPSULE_COMMON_KEYS | {
            "controls",
            "route_grammar",
            "complete_form_sha256",
        }
        allowed = required | {"required_controls_complete"}
        if not required <= frozenset(capsule) or not frozenset(capsule) <= allowed:
            raise ApplicationActionCompilerError(
                "exact_postcondition_failure", "form capsule fields are invalid"
            )
        if not isinstance(capsule["route_grammar"], str) or not capsule["route_grammar"]:
            raise ApplicationActionCompilerError(
                "exact_postcondition_failure", "form route grammar is invalid"
            )
        _digest(capsule["complete_form_sha256"], "complete form")
        if "required_controls_complete" in capsule and not isinstance(
            capsule["required_controls_complete"], bool
        ):
            raise ApplicationActionCompilerError(
                "exact_postcondition_failure", "required-control proof is invalid"
            )
        controls = capsule["controls"]
        if not isinstance(controls, list) or not controls:
            raise ApplicationActionCompilerError(
                "unmapped_ui_or_question", "form has no mapped controls"
            )
        capsule["controls"] = [
            _validated_control(item, options=False) for item in controls
        ]
    elif surface == "options":
        expected = _CAPSULE_COMMON_KEYS | {"controls", "origin"}
        _exact_mapping(capsule, expected, "options capsule")
        origin = _exact_mapping(
            capsule["origin"],
            frozenset(
                {"combo_ref", "name", "role", "form_revision", "match_count"}
            ),
            "options origin",
        )
        if (
            not isinstance(origin["combo_ref"], str)
            or _REF_RE.fullmatch(origin["combo_ref"]) is None
            or not isinstance(origin["name"], str)
            or not origin["name"]
            or not isinstance(origin["role"], str)
            or not origin["role"]
            or origin["match_count"] != 1
        ):
            raise ApplicationActionCompilerError(
                "exact_postcondition_failure", "options origin is not exact"
            )
        _digest(origin["form_revision"], "options origin form revision")
        controls = capsule["controls"]
        if not isinstance(controls, list) or not controls:
            raise ApplicationActionCompilerError(
                "unmapped_ui_or_question", "fresh options are absent"
            )
        exact_controls = [
            _validated_control(item, options=True) for item in controls
        ]
        country_semantic_origin = (
            origin["name"] == "Country" and origin["role"] == "combo box"
        )
        semantic_tokens = [
            control["semantic_token"]
            for control in exact_controls
            if "semantic_token" in control
        ]
        if country_semantic_origin:
            if len(semantic_tokens) != len(exact_controls) or len(
                semantic_tokens
            ) != len(set(semantic_tokens)):
                raise ApplicationActionCompilerError(
                    "exact_postcondition_failure",
                    "country option semantic tokens are incomplete or duplicate",
                )
        elif semantic_tokens:
            raise ApplicationActionCompilerError(
                "exact_postcondition_failure",
                "semantic tokens exceed the options origin contract",
            )
        capsule["controls"] = exact_controls
        capsule["origin"] = dict(origin)
    else:
        expected = _CAPSULE_COMMON_KEYS | {"mapped"}
        _exact_mapping(capsule, expected, "native capsule")
        mapped = capsule["mapped"]
        if not isinstance(mapped, Mapping) or any(
            not isinstance(key, str) for key in mapped
        ):
            raise ApplicationActionCompilerError(
                "exact_postcondition_failure", "native mapped controls are invalid"
            )
        exact_mapped: dict[str, list[dict[str, Any]]] = {}
        refs: set[str] = set()
        for key, raw_items in mapped.items():
            if not isinstance(raw_items, list):
                raise ApplicationActionCompilerError(
                    "exact_postcondition_failure", "native mapped group is invalid"
                )
            items: list[dict[str, Any]] = []
            for raw in raw_items:
                item = _exact_mapping(
                    raw,
                    frozenset({"key", "ref", "role", "states"}),
                    "native mapped control",
                )
                ref = item["ref"]
                if (
                    item["key"] != key
                    or not isinstance(ref, str)
                    or _REF_RE.fullmatch(ref) is None
                    or ref in refs
                    or not isinstance(item["role"], str)
                    or not item["role"]
                    or not isinstance(item["states"], list)
                    or any(not isinstance(state, str) for state in item["states"])
                ):
                    raise ApplicationActionCompilerError(
                        "exact_postcondition_failure", "native mapped identity differs"
                    )
                refs.add(ref)
                items.append(dict(item))
            exact_mapped[key] = items
        capsule["mapped"] = exact_mapped
    refs = [control["ref"] for control in capsule.get("controls", [])]
    if len(refs) != len(set(refs)):
        raise ApplicationActionCompilerError(
            "exact_postcondition_failure", "surface control refs repeat"
        )
    return capsule


def _fact(context: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    facts = context.get("applicant_facts")
    if not isinstance(facts, Mapping) or key not in facts:
        raise ApplicationActionCompilerError(
            "missing_truthful_applicant_data", "selected fact is absent"
        )
    fact = facts[key]
    if not isinstance(fact, Mapping) or frozenset(fact) != {"value", "evidence_sha256"}:
        raise ApplicationActionCompilerError(
            "missing_truthful_applicant_data", "selected fact is malformed"
        )
    _digest(fact["evidence_sha256"], "fact evidence")
    return fact


def _work_evidence(context: Mapping[str, Any], keys: list[str]) -> None:
    evidence = context.get("work_evidence")
    if not isinstance(evidence, Mapping):
        raise ApplicationActionCompilerError(
            "missing_truthful_applicant_data", "work evidence is absent"
        )
    for key in keys:
        claim = evidence.get(key)
        if (
            not isinstance(claim, Mapping)
            or frozenset(claim) != {"statement", "evidence_sha256"}
            or not isinstance(claim["statement"], str)
            or not claim["statement"]
        ):
            raise ApplicationActionCompilerError(
                "missing_truthful_applicant_data", "selected work evidence is absent"
            )
        _digest(claim["evidence_sha256"], "work evidence")


def _material_artifacts(context: Mapping[str, Any]) -> list[object]:
    stages = context.get("stages")
    materials = stages.get("materials") if isinstance(stages, Mapping) else None
    bindings = materials.get("artifacts") if isinstance(materials, Mapping) else None
    if not isinstance(bindings, list):
        raise ApplicationActionCompilerError(
            "missing_truthful_applicant_data", "material artifacts are absent"
        )
    return bindings


def _artifact(private_root: Path, context: Mapping[str, Any], slot: str) -> dict[str, str]:
    bindings = _material_artifacts(context)
    matches = [
        item
        for item in bindings
        if isinstance(item, Mapping) and item.get("kind") == slot
    ]
    if len(matches) != 1:
        raise ApplicationActionCompilerError(
            "missing_truthful_applicant_data", "artifact slot is not exact"
        )
    binding = matches[0]
    if frozenset(binding) != {"kind", "ref", "sha256", "media_type"}:
        raise ApplicationActionCompilerError(
            "missing_truthful_applicant_data", "artifact binding is malformed"
        )
    try:
        path = resolve_private_reference(private_root, binding["ref"], "application artifact")
        raw = _read_owned_file(path, 0o400, "application artifact")
    except IntakeContractError as exc:
        raise ApplicationActionCompilerError(
            "missing_truthful_applicant_data", "application artifact is unavailable"
        ) from exc
    expected = _digest(binding["sha256"], "application artifact")
    if sha256_hex(raw) != expected:
        raise ApplicationActionCompilerError(
            "missing_truthful_applicant_data", "application artifact digest differs"
        )
    return {
        "slot": slot,
        "name": path.name,
        "path": str(path),
        "sha256": expected,
    }


def _control(capsule: Mapping[str, Any], ref: str) -> Mapping[str, Any]:
    matches = [item for item in capsule.get("controls", []) if item["ref"] == ref]
    if len(matches) != 1:
        raise ApplicationActionCompilerError(
            "unmapped_ui_or_question", "decision ref is not uniquely current"
        )
    return matches[0]


def _mapped_native(capsule: Mapping[str, Any], key: str, ref: str) -> None:
    mapped = capsule.get("mapped")
    matches = [
        item
        for item in (mapped.get(key, []) if isinstance(mapped, Mapping) else [])
        if item["ref"] == ref
    ]
    if len(matches) != 1:
        raise ApplicationActionCompilerError(
            "unmapped_ui_or_question", "native decision ref is not uniquely current"
        )


def _ensure_seat_parent(private_root: Path, bucket_name: str, seat_id: str) -> Path:
    bucket = _ensure_bucket(private_root, bucket_name)
    parent = bucket / seat_id
    created = False
    try:
        os.mkdir(parent, 0o700)
        created = True
    except FileExistsError:
        pass
    except OSError as exc:
        raise IntakeContractError(
            "preparation_write_indeterminate", "action seat parent could not be created"
        ) from exc
    _directory_metadata(parent, "action seat parent")
    if created:
        _fsync_directory(bucket, "action bucket")
    return parent


class GreenhouseActionCompiler:
    def __init__(
        self,
        *,
        private_root_value: str | os.PathLike[str],
        seat_id_value: object,
        display_value: object,
        hands_commit_value: object,
    ) -> None:
        try:
            self._private_root = validate_private_root(private_root_value)
            self._seat_id = validate_public_id(seat_id_value, "seat ID")
            self._hands_commit = validate_git_commit(
                hands_commit_value, "Hands commit"
            )
        except IntakeContractError as exc:
            raise _compiler_error(exc) from exc
        if self._hands_commit != REQUIRED_HANDS_COMMIT:
            raise ApplicationActionCompilerError(
                "policy_or_authority_boundary", "Hands commit is not the reviewed contract"
            )
        if not isinstance(display_value, str) or _DISPLAY_RE.fullmatch(display_value) is None:
            raise ApplicationActionCompilerError(
                "policy_or_authority_boundary", "display is invalid"
            )
        self._display = display_value
        self._last_action_kind: str | None = None
        self._active_artifact_slot: str | None = None
        self._uploaded_slots: set[str] = set()
        self._transaction_id: str | None = None
        self._compiled_count = 0

    def decision_context(
        self,
        request: OneActionRequest,
        *,
        surface_capsule: object,
    ) -> GreenhouseDecisionContext:
        if not isinstance(request, OneActionRequest):
            raise ApplicationActionCompilerError(
                "policy_or_authority_boundary", "one-action request is invalid"
            )
        if (
            request.sequence_number != self._compiled_count + 1
            or request.sequence_number < 2
            or request.previous_receipt_sha256 is None
            or self._last_action_kind is None
        ):
            raise ApplicationActionCompilerError(
                "policy_or_authority_boundary", "decision sequence is not exact"
            )
        validate_application_envelope_sources(self._private_root, request.envelope)
        context = _validated_context(self._private_root, request)
        capsule = _validated_surface_capsule(surface_capsule, request)
        facts = context["applicant_facts"]
        evidence = context["work_evidence"]
        assert isinstance(facts, Mapping)
        assert isinstance(evidence, Mapping)
        fact_keys = tuple(sorted(_token(key, "fact key") for key in facts))
        evidence_keys = tuple(
            sorted(_token(key, "work evidence key") for key in evidence)
        )
        for key in fact_keys:
            _fact(context, key)
        _work_evidence(context, list(evidence_keys))
        return GreenhouseDecisionContext(
            application_identity_sha256=request.envelope.application_identity_sha256,
            surface_capsule=capsule,
            available_fact_keys=fact_keys,
            available_work_evidence_keys=evidence_keys,
            previous_action_kind=self._last_action_kind,
        )

    def compile(
        self,
        request: OneActionRequest,
        *,
        event_id_value: object,
        correlation_id_value: object,
        surface_capsule: object | None,
        decision: object | None,
    ) -> CompiledGreenhouseAction:
        try:
            return self._compile(
                request,
                event_id_value=event_id_value,
                correlation_id_value=correlation_id_value,
                surface_capsule=surface_capsule,
                decision=decision,
            )
        except (
            ApplicationActionCompilerError,
            ApplicationContractError,
            IntakeContractError,
            KeyError,
            OSError,
            TypeError,
            ValueError,
        ) as exc:
            raise _compiler_error(exc) from exc

    def _compile(
        self,
        request: OneActionRequest,
        *,
        event_id_value: object,
        correlation_id_value: object,
        surface_capsule: object | None,
        decision: object | None,
    ) -> CompiledGreenhouseAction:
        if not isinstance(request, OneActionRequest):
            raise ApplicationActionCompilerError(
                "policy_or_authority_boundary", "one-action request is invalid"
            )
        try:
            event_id = validate_public_id(event_id_value, "event ID")
            correlation_id = validate_public_id(correlation_id_value, "correlation ID")
        except IntakeContractError as exc:
            raise ApplicationActionCompilerError(
                "policy_or_authority_boundary", "turn lineage is invalid"
            ) from exc
        if request.envelope.provider != "greenhouse":
            raise ApplicationActionCompilerError(
                "policy_or_authority_boundary", "provider is not Greenhouse"
            )
        if request.sequence_number != self._compiled_count + 1:
            raise ApplicationActionCompilerError(
                "policy_or_authority_boundary", "one-action sequence is not contiguous"
            )
        validate_application_envelope_sources(self._private_root, request.envelope)
        context = _validated_context(self._private_root, request)
        if self._transaction_id is None:
            self._transaction_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    "https://palios-taey.org/application/"
                    f"{request.envelope_sha256}",
                )
            )
        if request.sequence_number == 1:
            if (
                request.previous_receipt_sha256 is not None
                or surface_capsule is not None
                or decision is not None
                or self._last_action_kind is not None
            ):
                raise ApplicationActionCompilerError(
                    "policy_or_authority_boundary", "initial action state is not exact"
                )
            action = {"kind": "observe_form"}
            decision_sha256 = sha256_hex(
                canonical_json_bytes({"schema": DECISION_SCHEMA, "action": "observe_form"})
            )
            capsule_sha256 = None
        else:
            if request.previous_receipt_sha256 is None or self._last_action_kind is None:
                raise ApplicationActionCompilerError(
                    "side_effect_uncertainty", "prior action receipt state is absent"
                )
            capsule = _validated_surface_capsule(surface_capsule, request)
            exact_decision = _validated_decision(decision)
            if exact_decision["revision"] != capsule["revision"]:
                raise ApplicationActionCompilerError(
                    "exact_postcondition_failure", "decision revision is stale"
                )
            _work_evidence(context, exact_decision["work_evidence_keys"])
            action = self._compile_decision(context, capsule, exact_decision)
            decision_sha256 = sha256_hex(canonical_json_bytes(exact_decision))
            capsule_sha256 = sha256_hex(canonical_json_bytes(capsule))
        action_id = str(
            uuid.uuid5(
                uuid.UUID(self._transaction_id),
                f"{request.sequence_number}:{correlation_id}:"
                f"{decision_sha256}:{capsule_sha256 or 'initial'}",
            )
        )
        frozen_action = {
            "schema": FROZEN_ACTION_SCHEMA,
            "provider": "greenhouse",
            "transaction_id": self._transaction_id,
            "action_id": action_id,
            "application_identity_sha256": (
                request.envelope.application_identity_sha256
            ),
            "expected_prior_event_hash": request.previous_receipt_sha256,
            "action": action,
        }
        frozen_raw = canonical_json_bytes(frozen_action)
        frozen_sha256 = sha256_hex(frozen_raw)
        action_parent = _ensure_seat_parent(
            self._private_root, "actions", self._seat_id
        )
        manifest_parent = _ensure_seat_parent(
            self._private_root, "transactions", self._seat_id
        )
        action_path = action_parent / f"{correlation_id}.json"
        manifest_path = manifest_parent / f"{correlation_id}.json"
        if _path_exists(action_path) or _path_exists(manifest_path):
            raise ApplicationActionCompilerError(
                "policy_or_authority_boundary", "action turn identity is spent"
            )
        manifest = {
            "schema": PRESENCE_MANIFEST_SCHEMA,
            "seat_id": self._seat_id,
            "event_id": event_id,
            "correlation_id": correlation_id,
            "platform": "greenhouse",
            "display": self._display,
            "hands_commit": self._hands_commit,
            "frozen_action_path": str(action_path),
            "frozen_action_sha256": frozen_sha256,
        }
        manifest_raw = canonical_json_bytes(manifest)
        try:
            _write_frozen_bytes(action_path, frozen_raw, "Greenhouse frozen action")
            _write_frozen_bytes(
                manifest_path, manifest_raw, "Greenhouse Presence manifest"
            )
            readback_exact = (
                stat.S_IMODE(os.lstat(action_path).st_mode) == 0o400
                and stat.S_IMODE(os.lstat(manifest_path).st_mode) == 0o400
                and read_private_json(action_path, "Greenhouse frozen action")[1]
                == frozen_raw
                and read_private_json(
                    manifest_path, "Greenhouse Presence manifest"
                )[1]
                == manifest_raw
            )
        except (IntakeContractError, OSError) as exc:
            raise ApplicationActionCompilerError(
                "side_effect_uncertainty", "compiled action readback became uncertain"
            ) from exc
        if not readback_exact:
            raise ApplicationActionCompilerError(
                "side_effect_uncertainty", "compiled action readback was not proven"
            )
        self._last_action_kind = str(action["kind"])
        self._compiled_count += 1
        if action["kind"] == "open_upload":
            self._active_artifact_slot = str(action["slot"])
        elif action["kind"] == "chooser_confirm":
            assert self._active_artifact_slot is not None
            self._uploaded_slots.add(self._active_artifact_slot)
            self._active_artifact_slot = None
        return CompiledGreenhouseAction(
            seat_id=self._seat_id,
            event_id=event_id,
            correlation_id=correlation_id,
            display=self._display,
            transaction_id=self._transaction_id,
            action_id=action_id,
            action_kind=str(action["kind"]),
            decision_sha256=decision_sha256,
            surface_capsule_sha256=capsule_sha256,
            frozen_action_sha256=frozen_sha256,
            presence_manifest_sha256=sha256_hex(manifest_raw),
        )

    def _compile_decision(
        self,
        context: Mapping[str, Any],
        capsule: Mapping[str, Any],
        decision: Mapping[str, Any],
    ) -> dict[str, Any]:
        kind = str(decision["action"])
        revision = str(decision["revision"])
        surface = capsule["surface"]
        if surface == "options":
            if kind != "select_option" or self._last_action_kind != "open_combo":
                raise ApplicationActionCompilerError(
                    "unmapped_ui_or_question", "fresh options require one exact selection"
                )
            fact = _fact(context, str(decision["fact_key"]))
            value = fact["value"]
            if not isinstance(value, str) or not value:
                raise ApplicationActionCompilerError(
                    "missing_truthful_applicant_data",
                    "fresh option lacks an exact truthful value",
                )
            match_key = (
                "semantic_token"
                if capsule["origin"]["name"] == "Country"
                and capsule["origin"]["role"] == "combo box"
                else "name"
            )
            matches = [
                control
                for control in capsule["controls"]
                if control[match_key] == value
            ]
            if len(matches) != 1:
                raise ApplicationActionCompilerError(
                    "unmapped_ui_or_question",
                    "fresh options do not contain one exact truthful match",
                )
            ref = str(matches[0]["ref"])
            expected_option_name = str(matches[0]["name"])
            return {
                "kind": "select_option",
                "ref": ref,
                "revision": revision,
                "combo_ref": capsule["origin"]["combo_ref"],
                "expected_option_name": expected_option_name,
            }
        ref = str(decision["ref"])
        if surface == "native_dialog":
            expected = _NATIVE_SEQUENCE.get(self._last_action_kind or "")
            if expected is None or kind != expected[0] or self._active_artifact_slot is None:
                raise ApplicationActionCompilerError(
                    "unmapped_ui_or_question", "native chooser sequence is not exact"
                )
            _mapped_native(capsule, expected[1], ref)
            action: dict[str, Any] = {
                "kind": kind,
                "ref": ref,
                "revision": revision,
            }
            if kind in {"chooser_type_path", "chooser_confirm"}:
                action["artifact"] = _artifact(
                    self._private_root, context, self._active_artifact_slot
                )
            return action
        if surface != "form":
            raise ApplicationActionCompilerError(
                "unmapped_ui_or_question", "surface is not actionable"
            )
        control = _control(capsule, ref)
        if kind not in control["operations"]:
            raise ApplicationActionCompilerError(
                "unmapped_ui_or_question", "decision operation is not currently allowed"
            )
        if kind in {"focus", "scroll_combo", "open_combo"}:
            _fact(context, str(decision["fact_key"]))
            return {"kind": kind, "ref": ref, "revision": revision}
        if kind == "fill":
            fact = _fact(context, str(decision["fact_key"]))
            value = fact["value"]
            if not isinstance(value, str) or not value:
                raise ApplicationActionCompilerError(
                    "missing_truthful_applicant_data", "fill value lacks exact rendered text"
                )
            return {
                "kind": "fill",
                "ref": ref,
                "revision": revision,
                "value": value,
                "value_sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
            }
        if kind == "activate_choice":
            fact = _fact(context, str(decision["fact_key"]))
            if fact["value"] is not True:
                raise ApplicationActionCompilerError(
                    "missing_truthful_applicant_data", "choice is not truthfully affirmative"
                )
            expected_state = (
                "selected" if control["role"] == "radio button" else "checked"
            )
            return {
                "kind": "activate_choice",
                "ref": ref,
                "revision": revision,
                "expected_state": expected_state,
            }
        if kind == "open_upload":
            slot = control.get("artifact_slot")
            if slot not in {"resume", "cover"} or self._active_artifact_slot is not None:
                raise ApplicationActionCompilerError(
                    "unmapped_ui_or_question", "upload slot is not exact"
                )
            _artifact(self._private_root, context, str(slot))
            return {
                "kind": "open_upload",
                "ref": ref,
                "revision": revision,
                "slot": slot,
            }
        if kind == "submit":
            if capsule.get("required_controls_complete") is not True:
                raise ApplicationActionCompilerError(
                    "exact_postcondition_failure",
                    "surface capsule lacks required_controls_complete=true",
                )
            artifacts = [
                _artifact(self._private_root, context, slot)
                for slot in ("resume", "cover")
                if any(
                    isinstance(item, Mapping) and item.get("kind") == slot
                    for item in _material_artifacts(context)
                )
            ]
            artifact_slots = {item["slot"] for item in artifacts}
            if "resume" not in artifact_slots or not artifact_slots <= self._uploaded_slots:
                raise ApplicationActionCompilerError(
                    "missing_truthful_applicant_data", "required artifact proof is incomplete"
                )
            policy = context.get("submission_policy")
            if not isinstance(policy, Mapping) or policy.get("submission_authorized") is not True:
                raise ApplicationActionCompilerError(
                    "policy_or_authority_boundary", "submission is not authorized"
                )
            return {
                "kind": "submit",
                "ref": ref,
                "revision": revision,
                "precondition": {
                    "required_controls_complete": True,
                    "truth_attested": True,
                    "complete_form_sha256": capsule["complete_form_sha256"],
                    "truth_attestation_sha256": context["truth_attestation_sha256"],
                    "artifacts": artifacts,
                },
            }
        raise ApplicationActionCompilerError(
            "unmapped_ui_or_question", "form action is unsupported"
        )


__all__ = [
    "ApplicationActionCompilerError",
    "CompiledGreenhouseAction",
    "DECISION_SCHEMA",
    "GreenhouseActionCompiler",
    "GreenhouseDecisionContext",
    "REQUIRED_HANDS_COMMIT",
]
