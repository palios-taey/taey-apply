from __future__ import annotations

import argparse
import json
import sys

from .classification_preparer import (
    ClassificationPreparationError,
    prepare_classification_claim,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare one frozen private LinkedIn classification claim.",
    )
    parser.add_argument("--private-root", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--manifest-file", required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    return parser


def run(args: argparse.Namespace) -> dict[str, object]:
    return prepare_classification_claim(
        private_root_value=args.private_root,
        database_path_value=args.database,
        manifest_path_value=args.manifest_file,
        expected_manifest_sha256=args.expected_manifest_sha256,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run(args)
    except ClassificationPreparationError as exc:
        sys.stderr.write(
            f"ClassificationPreparationError[{exc.failure_code}]: preparation stopped\n"
        )
        return 2
    sys.stdout.write(
        json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
