from __future__ import annotations

import argparse
import json
import sys

from .contract import IntakeContractError
from .preparer import prepare_linkedin_intake


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare one frozen LinkedIn intake transaction without database access.",
    )
    parser.add_argument("--private-root", required=True)
    parser.add_argument("--draft-file", required=True)
    parser.add_argument("--seat-id", required=True)
    parser.add_argument("--correlation-id", required=True)
    return parser


def run(args: argparse.Namespace) -> dict[str, object]:
    return prepare_linkedin_intake(
        private_root_value=args.private_root,
        draft_path_value=args.draft_file,
        seat_id_value=args.seat_id,
        correlation_id_value=args.correlation_id,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run(args)
    except IntakeContractError as exc:
        sys.stderr.write(
            f"IntakeContractError[{exc.failure_code}]: preparation stopped\n"
        )
        return 2
    sys.stdout.write(
        json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
