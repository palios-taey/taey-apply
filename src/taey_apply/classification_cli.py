from __future__ import annotations

import argparse
import json
import sys

from .classification_contract import (
    ClassificationContractError,
    read_classification_claim,
    reserve_classification_attempt,
)
from .contract import (
    IntakeContractError,
    sha256_hex,
    turn_lineage_sha256,
    validate_database_path,
    validate_digest,
    validate_new_receipt_path,
    validate_private_root,
    validate_process_generation,
    validate_public_id,
)
from .linkedin_classification import (
    finalize_classification,
    load_qualified_intake,
    persist_classification,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Commit one frozen private LinkedIn classification claim.",
    )
    parser.add_argument("--private-root", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--claim-file", required=True)
    parser.add_argument("--expected-claim-sha256", required=True)
    parser.add_argument("--receipt-file", required=True)
    parser.add_argument("--requester", required=True)
    parser.add_argument("--turn-id", required=True)
    parser.add_argument("--correlation-id", required=True)
    parser.add_argument("--process-generation", required=True)
    return parser


def run(args: argparse.Namespace) -> dict[str, object]:
    try:
        private_root = validate_private_root(args.private_root)
        receipt_path = validate_new_receipt_path(args.receipt_file, private_root)
        database = validate_database_path(args.database)
        requester = validate_public_id(args.requester, "requester")
        turn_id = validate_public_id(args.turn_id, "turn ID")
        correlation_id = validate_public_id(args.correlation_id, "correlation ID")
        process_generation = validate_process_generation(args.process_generation)
        expected_claim_sha256 = validate_digest(
            args.expected_claim_sha256, "expected claim digest"
        )
    except IntakeContractError as exc:
        raise ClassificationContractError(
            "RUNTIME_CONTRACT_INVALID", "runtime contract is invalid"
        ) from exc
    lineage = turn_lineage_sha256(
        requester,
        turn_id,
        correlation_id,
        process_generation,
    )
    claim, transaction_sha256 = read_classification_claim(
        private_root,
        args.claim_file,
        expected_claim_sha256,
    )
    qualified = load_qualified_intake(private_root, claim)
    attempt_sha256 = reserve_classification_attempt(
        private_root, transaction_sha256
    )
    write = persist_classification(database, qualified, claim)
    return finalize_classification(
        receipt_path=receipt_path,
        requester=requester,
        transaction_sha256=transaction_sha256,
        attempt_sha256=attempt_sha256,
        turn_lineage_sha256=lineage,
        correlation_id_sha256=sha256_hex(correlation_id.encode("utf-8")),
        qualified=qualified,
        claim=claim,
        write=write,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run(args)
    except ClassificationContractError as exc:
        sys.stderr.write(
            f"ClassificationContractError[{exc.failure_code}]: transaction stopped\n"
        )
        return 2
    sys.stdout.write(
        json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
