from __future__ import annotations

import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping

from .application_contract import (
    ApplicationContractError,
    EVIDENCE_STATES,
    _digest,
    _exact_keys,
    _mapping,
    _private_reference,
)
from .contract import (
    IntakeContractError,
    _read_owned_file,
    canonical_json_bytes,
    read_private_json,
    resolve_private_reference,
    sha256_hex,
    validate_private_root,
    validate_public_id,
)
from .preparer import (
    _create_identity_parent,
    _directory_metadata,
    _ensure_bucket,
    _path_exists,
    _write_frozen_bytes,
)


MATERIALIZATION_MANIFEST_SCHEMA = (
    "taey_apply_application_materialization_manifest_v1"
)
MATERIALIZATION_OPERATION = "materialize_autonomous_application"
GATE_RECEIPT_SCHEMA = "taey_apply_application_gate_receipt_v1"
APPLICATION_CONTEXT_SCHEMA = "taey_apply_application_context_v1"
LIFECYCLE_SCHEMA = "taey_apply_application_lifecycle_v1"
LIFECYCLE_OPERATION = "execute_autonomous_application"
MATERIALIZATION_RESULT_SCHEMA = "taey_apply_application_materialization_result_v1"
MATERIALIZATION_REFUSAL_SCHEMA = (
    "taey_apply_application_materialization_refusal_v1"
)

_SOURCE_STAGES = (
    "discovery",
    "qualification",
    "deep_research",
    "materials",
)
_MANIFEST_KEYS = frozenset(
    {
        "schema",
        "operation",
        "provider",
        "application_identity_sha256",
        "maximum_one_action_calls",
        "required_fact_keys",
        "stages",
        "applicant_facts",
        "work_evidence",
        "submission_policy",
    }
)
_STAGE_KEYS = frozenset({"state", "artifacts"})
_ARTIFACT_KEYS = frozenset({"kind", "ref", "sha256", "media_type"})
_POLICY_KEYS = frozenset({"submission_authorized", "directives"})
_PROVIDER_RE = re.compile(r"[a-z][a-z0-9_-]{0,31}")
_TOKEN_RE = re.compile(r"[a-z][a-z0-9_]{0,127}")
_MEDIA_TYPE_RE = re.compile(
    r"[a-z0-9][a-z0-9!#$&^_.+-]{0,126}/[a-z0-9][a-z0-9!#$&^_.+-]{0,126}"
)
_RESERVED_AUTHORITY_KEYS = frozenset(
    {
        "approval_required",
        "human_approval",
        "human_review_required",
        "review_queue",
    }
)


class ApplicationMaterializationError(RuntimeError):
    def __init__(self, failure_code: str, message: str) -> None:
        super().__init__(message)
        self.failure_code = failure_code


def _token(value: object, context: str) -> str:
    if not isinstance(value, str) or _TOKEN_RE.fullmatch(value) is None:
        raise ApplicationMaterializationError(
            "application_materialization_invalid", f"{context} is invalid"
        )
    if value in _RESERVED_AUTHORITY_KEYS:
        raise ApplicationMaterializationError(
            "application_materialization_invalid", f"{context} is reserved"
        )
    return value


def _private_value(value: object, context: str) -> object:
    if isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, str) and value:
        return value
    if (
        isinstance(value, list)
        and value
        and all(isinstance(item, str) and item for item in value)
    ):
        return list(value)
    raise ApplicationMaterializationError(
        "missing_truthful_applicant_data", f"{context} has no exact private value"
    )


def _artifact_bindings(value: object, context: str) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise ApplicationMaterializationError(
            "application_materialization_invalid", f"{context} must be non-empty"
        )
    bindings: list[dict[str, str]] = []
    kinds: set[str] = set()
    references: set[str] = set()
    for index, raw in enumerate(value):
        item_context = f"{context}[{index}]"
        binding = _mapping(raw, item_context)
        _exact_keys(binding, _ARTIFACT_KEYS, item_context)
        kind = _token(binding["kind"], f"{item_context}.kind")
        reference = _private_reference(binding["ref"], f"{item_context}.ref")
        media_type = binding["media_type"]
        if not isinstance(media_type, str) or _MEDIA_TYPE_RE.fullmatch(media_type) is None:
            raise ApplicationMaterializationError(
                "application_materialization_invalid",
                f"{item_context}.media_type is invalid",
            )
        if kind in kinds or reference in references:
            raise ApplicationMaterializationError(
                "application_materialization_invalid", f"{context} repeats an artifact"
            )
        kinds.add(kind)
        references.add(reference)
        bindings.append(
            {
                "kind": kind,
                "ref": reference,
                "sha256": _digest(binding["sha256"], f"{item_context}.sha256"),
                "media_type": media_type,
            }
        )
    return bindings


def _validated_manifest(value: object) -> dict[str, Any]:
    manifest = _mapping(value, "materialization manifest")
    _exact_keys(manifest, _MANIFEST_KEYS, "materialization manifest")
    if (
        manifest["schema"] != MATERIALIZATION_MANIFEST_SCHEMA
        or manifest["operation"] != MATERIALIZATION_OPERATION
    ):
        raise ApplicationMaterializationError(
            "application_materialization_invalid", "materialization manifest is unsupported"
        )
    provider = manifest["provider"]
    if not isinstance(provider, str) or _PROVIDER_RE.fullmatch(provider) is None:
        raise ApplicationMaterializationError(
            "application_materialization_invalid", "provider is invalid"
        )
    identity = _digest(
        manifest["application_identity_sha256"], "application identity"
    )
    maximum = manifest["maximum_one_action_calls"]
    if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= 256:
        raise ApplicationMaterializationError(
            "application_materialization_invalid", "one-action budget is invalid"
        )
    raw_required = manifest["required_fact_keys"]
    if not isinstance(raw_required, list) or not raw_required:
        raise ApplicationMaterializationError(
            "missing_truthful_applicant_data", "required fact keys are absent"
        )
    required = [_token(item, "required fact key") for item in raw_required]
    if len(required) != len(set(required)):
        raise ApplicationMaterializationError(
            "application_materialization_invalid", "required fact keys repeat"
        )
    raw_stages = _mapping(manifest["stages"], "stages")
    if frozenset(raw_stages) != frozenset(_SOURCE_STAGES):
        raise ApplicationMaterializationError(
            "application_materialization_invalid", "source stages are not exact"
        )
    stages: dict[str, dict[str, object]] = {}
    stage_refs: set[str] = set()
    for stage in _SOURCE_STAGES:
        raw_stage = _mapping(raw_stages[stage], f"stages.{stage}")
        _exact_keys(raw_stage, _STAGE_KEYS, f"stages.{stage}")
        expected_state = EVIDENCE_STATES[stage]
        if raw_stage["state"] != expected_state:
            raise ApplicationMaterializationError(
                "application_materialization_invalid", f"{stage} is not complete"
            )
        artifacts = _artifact_bindings(
            raw_stage["artifacts"], f"stages.{stage}.artifacts"
        )
        refs = {item["ref"] for item in artifacts}
        if stage_refs.intersection(refs):
            raise ApplicationMaterializationError(
                "application_materialization_invalid", "source stages reuse an artifact"
            )
        stage_refs.update(refs)
        if stage == "materials":
            resume_count = sum(item["kind"] == "resume" for item in artifacts)
            if resume_count != 1:
                raise ApplicationMaterializationError(
                    "missing_truthful_applicant_data",
                    "materials must bind exactly one resume",
                )
        stages[stage] = {"state": expected_state, "artifacts": artifacts}
    facts = _validated_records(
        manifest["applicant_facts"],
        context="applicant facts",
        payload_key="value",
        failure_code="missing_truthful_applicant_data",
        required=required,
    )
    work_evidence = _validated_records(
        manifest["work_evidence"],
        context="work evidence",
        payload_key="statement",
        failure_code="missing_truthful_applicant_data",
    )
    policy = _mapping(manifest["submission_policy"], "submission policy")
    _exact_keys(policy, _POLICY_KEYS, "submission policy")
    if policy["submission_authorized"] is not True:
        raise ApplicationMaterializationError(
            "policy_or_authority_boundary", "submission authority is not exact"
        )
    directives = _validated_records(
        policy["directives"],
        context="submission directives",
        payload_key="value",
        failure_code="policy_or_authority_boundary",
    )
    return {
        "schema": MATERIALIZATION_MANIFEST_SCHEMA,
        "operation": MATERIALIZATION_OPERATION,
        "provider": provider,
        "application_identity_sha256": identity,
        "maximum_one_action_calls": maximum,
        "required_fact_keys": required,
        "stages": stages,
        "applicant_facts": facts,
        "work_evidence": work_evidence,
        "submission_policy": {
            "submission_authorized": True,
            "directives": directives,
        },
    }


def _load_manifest(
    private_root: Path,
    path_value: str | os.PathLike[str],
    expected_sha256: object,
) -> tuple[dict[str, Any], str]:
    path = Path(path_value)
    if not path.is_absolute() or os.path.normpath(str(path)) != str(path):
        raise ApplicationMaterializationError(
            "application_materialization_invalid",
            "materialization manifest path must be canonical and absolute",
        )
    try:
        relative = path.relative_to(private_root)
        resolved = resolve_private_reference(
            private_root, relative.as_posix(), "materialization manifest"
        )
        value, raw = read_private_json(resolved, "materialization manifest")
    except (IntakeContractError, ValueError) as exc:
        raise ApplicationMaterializationError(
            "application_materialization_invalid", "materialization manifest is invalid"
        ) from exc
    actual = sha256_hex(raw)
    if actual != _digest(expected_sha256, "materialization manifest digest"):
        raise ApplicationMaterializationError(
            "application_materialization_invalid",
            "materialization manifest digest differs",
        )
    return _validated_manifest(value), actual


def _verify_artifact(
    private_root: Path, binding: Mapping[str, str], context: str
) -> None:
    try:
        path = resolve_private_reference(private_root, binding["ref"], context)
        raw = _read_owned_file(path, 0o400, context)
    except IntakeContractError as exc:
        raise ApplicationMaterializationError(
            "application_materialization_invalid", f"{context} is invalid"
        ) from exc
    if sha256_hex(raw) != binding["sha256"]:
        raise ApplicationMaterializationError(
            "application_materialization_invalid", f"{context} digest differs"
        )


def _validated_records(
    value: object,
    *,
    context: str,
    payload_key: str,
    failure_code: str,
    required: list[str] | None = None,
) -> dict[str, dict[str, object]]:
    try:
        raw_records = _mapping(value, context)
        if not raw_records:
            raise ApplicationMaterializationError(
                failure_code, f"{context} records are absent"
            )
        records: dict[str, dict[str, object]] = {}
        for raw_key, raw_record in raw_records.items():
            key = _token(raw_key, f"{context} key")
            record = _mapping(raw_record, f"{context}.{key}")
            _exact_keys(
                record,
                frozenset({payload_key, "evidence_sha256"}),
                f"{context}.{key}",
            )
            payload = record[payload_key]
            if payload_key == "value":
                payload = _private_value(payload, f"{context}.{key}.value")
            elif not isinstance(payload, str) or not payload:
                raise ApplicationMaterializationError(
                    failure_code, f"{context}.{key}.{payload_key} is absent"
                )
            records[key] = {
                payload_key: payload,
                "evidence_sha256": _digest(
                    record["evidence_sha256"], f"{context}.{key}.evidence"
                ),
            }
    except ApplicationContractError as exc:
        raise ApplicationMaterializationError(
            failure_code, f"{context} is invalid"
        ) from exc
    if required and set(required) - set(records):
        raise ApplicationMaterializationError(
            failure_code, "required applicant facts are missing"
        )
    return records


def _derived_refs(seat_id: str, correlation_id: str) -> dict[str, str]:
    prefix = f"application-materializations/{seat_id}/{correlation_id}"
    return {
        "context_ref": f"{prefix}.context.json",
        "lifecycle_ref": f"{prefix}.lifecycle.json",
        "refusal_ref": f"{prefix}.refusal.json",
        **{
            f"{stage}_receipt_ref": f"{prefix}.{stage}.gate.json"
            for stage in EVIDENCE_STATES
        },
        "envelope_ref": f"application-envelopes/{seat_id}/{correlation_id}.json",
        "result_ref": f"application-results/{seat_id}/{correlation_id}.json",
        "preparation_refusal_ref": (
            f"application-preparation-refusals/{seat_id}/{correlation_id}.json"
        ),
    }


def _write_refusal(
    path: Path,
    *,
    seat_id: str,
    correlation_id: str,
    failure_code: str,
) -> None:
    _write_frozen_bytes(
        path,
        canonical_json_bytes(
            {
                "correlation_id": correlation_id,
                "failure_code": failure_code,
                "ok": False,
                "schema": MATERIALIZATION_REFUSAL_SCHEMA,
                "seat_id": seat_id,
                "state": "materialization_refused",
            }
        ),
        "application materialization refusal",
    )


def _materialization_failure(exc: BaseException) -> ApplicationMaterializationError:
    if isinstance(exc, ApplicationMaterializationError):
        return exc
    if isinstance(exc, ApplicationContractError):
        return ApplicationMaterializationError(
            "application_materialization_invalid", "materialization stopped"
        )
    if isinstance(exc, IntakeContractError):
        if exc.failure_code == "identity_spent":
            return ApplicationMaterializationError(
                "identity_spent", "materialization stopped"
            )
        if exc.failure_code == "preparation_write_indeterminate":
            return ApplicationMaterializationError(
                "materialization_write_indeterminate", "materialization stopped"
            )
        return ApplicationMaterializationError(
            "application_materialization_invalid", "materialization stopped"
        )
    return ApplicationMaterializationError(
        "materialization_write_indeterminate", "materialization stopped"
    )


def materialize_application_context(
    *,
    private_root_value: str | os.PathLike[str],
    manifest_path_value: str | os.PathLike[str],
    expected_manifest_sha256: object,
    seat_id_value: object,
    correlation_id_value: object,
) -> dict[str, object]:
    try:
        private_root = validate_private_root(private_root_value)
        seat_id = validate_public_id(seat_id_value, "seat ID")
        correlation_id = validate_public_id(correlation_id_value, "correlation ID")
    except IntakeContractError as exc:
        raise _materialization_failure(exc) from exc

    bucket_path = private_root / "application-materializations"
    parent_path = bucket_path / seat_id
    try:
        if _path_exists(parent_path):
            raise ApplicationMaterializationError(
                "identity_spent", "materialization identity already exists"
            )
    except IntakeContractError as exc:
        raise _materialization_failure(exc) from exc

    try:
        bucket = _ensure_bucket(private_root, "application-materializations")
        parent = _create_identity_parent(
            bucket, seat_id, "application materializations"
        )
        refs = _derived_refs(seat_id, correlation_id)
        refusal_path = private_root / refs["refusal_ref"]
        manifest, manifest_sha256 = _load_manifest(
            private_root, manifest_path_value, expected_manifest_sha256
        )
        for stage in _SOURCE_STAGES:
            for index, binding in enumerate(manifest["stages"][stage]["artifacts"]):
                _verify_artifact(
                    private_root, binding, f"{stage} artifact {index + 1}"
                )

        identity = manifest["application_identity_sha256"]
        facts = manifest["applicant_facts"]
        work_evidence = manifest["work_evidence"]
        submission_policy = manifest["submission_policy"]

        evidence_projections: dict[str, object] = {
            stage: manifest["stages"][stage] for stage in _SOURCE_STAGES
        }
        evidence_projections["truthful_applicant_data"] = {
            "applicant_facts": facts,
            "required_fact_keys": manifest["required_fact_keys"],
            "work_evidence": work_evidence,
        }
        evidence_projections["submission_authority"] = {
            "submission_policy": submission_policy
        }

        evidence: dict[str, dict[str, str]] = {}
        for stage, state in EVIDENCE_STATES.items():
            receipt_ref = refs[f"{stage}_receipt_ref"]
            receipt = {
                "schema": GATE_RECEIPT_SCHEMA,
                "ok": True,
                "state": state,
                "application_identity_sha256": identity,
                "evidence_sha256": sha256_hex(
                    canonical_json_bytes(evidence_projections[stage])
                ),
            }
            receipt_bytes = canonical_json_bytes(receipt)
            _write_frozen_bytes(
                private_root / receipt_ref,
                receipt_bytes,
                f"{stage} gate receipt",
            )
            evidence[stage] = {
                "receipt_ref": receipt_ref,
                "receipt_sha256": sha256_hex(receipt_bytes),
                "receipt_schema": GATE_RECEIPT_SCHEMA,
                "expected_state": state,
            }

        truth_attestation_sha256 = sha256_hex(
            canonical_json_bytes(
                {
                    "application_identity_sha256": identity,
                    "applicant_facts_sha256": sha256_hex(canonical_json_bytes(facts)),
                    "required_fact_keys": manifest["required_fact_keys"],
                    "submission_policy_sha256": sha256_hex(
                        canonical_json_bytes(submission_policy)
                    ),
                    "work_evidence_sha256": sha256_hex(
                        canonical_json_bytes(work_evidence)
                    ),
                }
            )
        )
        context = {
            "schema": APPLICATION_CONTEXT_SCHEMA,
            "provider": manifest["provider"],
            "application_identity_sha256": identity,
            "source_manifest_sha256": manifest_sha256,
            "required_fact_keys": manifest["required_fact_keys"],
            "stages": manifest["stages"],
            "applicant_facts": facts,
            "work_evidence": work_evidence,
            "submission_policy": submission_policy,
            "truth_attestation_sha256": truth_attestation_sha256,
        }
        context_bytes = canonical_json_bytes(context)
        context_ref = refs["context_ref"]
        _write_frozen_bytes(
            private_root / context_ref, context_bytes, "application context"
        )
        context_sha256 = sha256_hex(context_bytes)

        lifecycle = {
            "schema": LIFECYCLE_SCHEMA,
            "operation": LIFECYCLE_OPERATION,
            "provider": manifest["provider"],
            "application_identity_sha256": identity,
            "evidence": evidence,
            "application_context_ref": context_ref,
            "application_context_sha256": context_sha256,
            "application_context_schema": APPLICATION_CONTEXT_SCHEMA,
            "maximum_one_action_calls": manifest["maximum_one_action_calls"],
            "envelope_ref": refs["envelope_ref"],
            "result_ref": refs["result_ref"],
            "refusal_ref": refs["preparation_refusal_ref"],
        }
        lifecycle_bytes = canonical_json_bytes(lifecycle)
        lifecycle_ref = refs["lifecycle_ref"]
        lifecycle_sha256 = sha256_hex(lifecycle_bytes)

        for label, path in {
            "context": private_root / context_ref,
            **{
                stage: private_root / evidence[stage]["receipt_ref"]
                for stage in EVIDENCE_STATES
            },
        }.items():
            _value, raw = read_private_json(path, f"{label} readback")
            if stat.S_IMODE(os.lstat(path).st_mode) != 0o400 or not raw:
                raise ApplicationMaterializationError(
                    "materialization_write_indeterminate",
                    "materialization readback was not proven",
                )
        parent_mode = (
            f"{stat.S_IMODE(_directory_metadata(parent, 'materialization parent').st_mode):04o}"
        )
        if _path_exists(refusal_path):
            raise ApplicationMaterializationError(
                "materialization_write_indeterminate",
                "materialization refusal appeared during success",
            )
        _write_frozen_bytes(
            private_root / lifecycle_ref,
            lifecycle_bytes,
            "application lifecycle",
        )
        return {
            "schema": MATERIALIZATION_RESULT_SCHEMA,
            "ok": True,
            "state": "materialized",
            "seat_id": seat_id,
            "correlation_id": correlation_id,
            "application_identity_sha256": identity,
            "source_manifest_sha256": manifest_sha256,
            "context_sha256": context_sha256,
            "lifecycle_sha256": lifecycle_sha256,
            "truth_attestation_sha256": truth_attestation_sha256,
            "evidence_gate_count": len(evidence),
            "fact_count": len(facts),
            "work_evidence_count": len(work_evidence),
            "maximum_one_action_calls": manifest["maximum_one_action_calls"],
            "file_mode": "0400",
            "parent_mode": parent_mode,
            "refusal_absent": True,
        }
    except (
        ApplicationContractError,
        ApplicationMaterializationError,
        IntakeContractError,
        OSError,
    ) as exc:
        failure = _materialization_failure(exc)
        if "refusal_path" in locals() and not _path_exists(refusal_path):
            _write_refusal(
                refusal_path,
                seat_id=seat_id,
                correlation_id=correlation_id,
                failure_code=failure.failure_code,
            )
        raise failure from exc


__all__ = [
    "APPLICATION_CONTEXT_SCHEMA",
    "ApplicationMaterializationError",
    "GATE_RECEIPT_SCHEMA",
    "MATERIALIZATION_MANIFEST_SCHEMA",
    "MATERIALIZATION_RESULT_SCHEMA",
    "materialize_application_context",
]
