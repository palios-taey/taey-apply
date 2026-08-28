from __future__ import annotations

import argparse
import json
import sys

from .application_contract import ApplicationContractError
from .application_preparer import prepare_application


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare one autonomous application envelope without UI access.",
    )
    parser.add_argument("--private-root", required=True)
    parser.add_argument("--lifecycle-file", required=True)
    parser.add_argument("--lifecycle-sha256", required=True)
    parser.add_argument("--seat-id", required=True)
    parser.add_argument("--correlation-id", required=True)
    return parser


def run(args: argparse.Namespace) -> dict[str, object]:
    return prepare_application(
        private_root_value=args.private_root,
        lifecycle_path_value=args.lifecycle_file,
        expected_lifecycle_sha256=args.lifecycle_sha256,
        seat_id_value=args.seat_id,
        correlation_id_value=args.correlation_id,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run(args)
    except ApplicationContractError as exc:
        sys.stderr.write(
            f"ApplicationContractError[{exc.failure_code}]: preparation stopped\n"
        )
        return 2
    sys.stdout.write(
        json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
