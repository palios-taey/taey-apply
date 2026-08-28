from __future__ import annotations

import argparse
import json
import sys

from .application_materializer import (
    ApplicationMaterializationError,
    materialize_application_context,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Materialize one private autonomous-application context and lifecycle "
            "without UI or database access."
        ),
    )
    parser.add_argument("--private-root", required=True)
    parser.add_argument("--manifest-file", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--seat-id", required=True)
    parser.add_argument("--correlation-id", required=True)
    return parser


def run(args: argparse.Namespace) -> dict[str, object]:
    return materialize_application_context(
        private_root_value=args.private_root,
        manifest_path_value=args.manifest_file,
        expected_manifest_sha256=args.manifest_sha256,
        seat_id_value=args.seat_id,
        correlation_id_value=args.correlation_id,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run(args)
    except ApplicationMaterializationError as exc:
        sys.stderr.write(
            "ApplicationMaterializationError"
            f"[{exc.failure_code}]: materialization stopped\n"
        )
        return 2
    sys.stdout.write(
        json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
