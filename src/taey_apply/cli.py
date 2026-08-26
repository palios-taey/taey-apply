from __future__ import annotations

import argparse
import json
import sys

from .contract import (
    IntakeContractError,
    read_private_input,
    sha256_hex,
    turn_lineage_sha256,
    validate_database_path,
    validate_digest,
    validate_new_receipt_path,
    validate_private_root,
    validate_process_generation,
    validate_public_id,
)
from .linkedin_intake import finalize_success, load_linkedin_capture, persist_capture


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ingest one receipt-bound LinkedIn capture into a private jobs database.",
    )
    parser.add_argument("--private-root", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--transaction-file", required=True)
    parser.add_argument("--expected-transaction-sha256", required=True)
    parser.add_argument("--receipt-file", required=True)
    parser.add_argument("--requester", required=True)
    parser.add_argument("--turn-id", required=True)
    parser.add_argument("--correlation-id", required=True)
    parser.add_argument("--process-generation", required=True)
    return parser


def run(args: argparse.Namespace) -> dict[str, object]:
    private_root = validate_private_root(args.private_root)
    receipt_path = validate_new_receipt_path(args.receipt_file, private_root)
    database = validate_database_path(args.database)
    requester = validate_public_id(args.requester, "requester")
    turn_id = validate_public_id(args.turn_id, "turn ID")
    correlation_id = validate_public_id(args.correlation_id, "correlation ID")
    process_generation = validate_process_generation(args.process_generation)
    expected_transaction_sha256 = validate_digest(
        args.expected_transaction_sha256,
        "expected transaction digest",
    )
    lineage = turn_lineage_sha256(
        requester,
        turn_id,
        correlation_id,
        process_generation,
    )
    transaction, transaction_sha256 = read_private_input(
        args.transaction_file,
        private_root,
        expected_transaction_sha256,
    )
    capture = load_linkedin_capture(private_root, transaction)
    write = persist_capture(database, capture)
    return finalize_success(
        receipt_path=receipt_path,
        requester=requester,
        transaction_sha256=transaction_sha256,
        turn_lineage_sha256=lineage,
        correlation_id_sha256=sha256_hex(correlation_id.encode("utf-8")),
        capture=capture,
        write=write,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run(args)
    except IntakeContractError as exc:
        sys.stderr.write(
            f"IntakeContractError[{exc.failure_code}]: transaction stopped\n"
        )
        return 2
    sys.stdout.write(
        json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
