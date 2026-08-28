from __future__ import annotations

import argparse
import json
import sys

from .application_contract import ApplicationContractError
from .application_executor import (
    ApplicationExecutorError,
    GreenhousePresenceOneActionExecutor,
    SingleRequestJsonTransport,
    TaeyJsonSchemaDecisionClient,
)
from .application_runner import run_application


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one autonomous Greenhouse application to exact confirmation"
    )
    parser.add_argument("--private-root", required=True)
    parser.add_argument("--envelope-file", required=True)
    parser.add_argument("--envelope-sha256", required=True)
    parser.add_argument("--seat-id", required=True)
    parser.add_argument("--display", required=True)
    parser.add_argument("--hands-commit", required=True)
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--correlation-id", required=True)
    parser.add_argument("--taey-decision-endpoint", required=True)
    parser.add_argument("--taey-model", required=True)
    parser.add_argument("--presence-endpoint", required=True)
    parser.add_argument("--decision-timeout-seconds", type=int, default=180)
    parser.add_argument("--presence-timeout-seconds", type=int, default=300)
    return parser


def run(args: argparse.Namespace) -> dict:
    decision_transport = SingleRequestJsonTransport(
        timeout_seconds=args.decision_timeout_seconds
    )
    presence_transport = SingleRequestJsonTransport(
        timeout_seconds=args.presence_timeout_seconds
    )
    decision_source = TaeyJsonSchemaDecisionClient(
        endpoint_value=args.taey_decision_endpoint,
        model_value=args.taey_model,
        transport=decision_transport,
    )
    executor = GreenhousePresenceOneActionExecutor(
        private_root_value=args.private_root,
        seat_id_value=args.seat_id,
        display_value=args.display,
        hands_commit_value=args.hands_commit,
        event_id_value=args.event_id,
        correlation_id_value=args.correlation_id,
        presence_endpoint_value=args.presence_endpoint,
        decision_source=decision_source,
        presence_transport=presence_transport,
    )
    return run_application(
        private_root_value=args.private_root,
        envelope_path_value=args.envelope_file,
        expected_envelope_sha256=args.envelope_sha256,
        executor=executor,
    )


def main(argv: list[str] | None = None) -> int:
    try:
        result = run(build_parser().parse_args(argv))
    except ApplicationExecutorError as exc:
        sys.stderr.write(
            f"ApplicationExecutorError[{exc.failure_code}]: execution stopped\n"
        )
        return 2
    except ApplicationContractError as exc:
        sys.stderr.write(
            f"ApplicationContractError[{exc.failure_code}]: execution stopped\n"
        )
        return 2
    sys.stdout.write(
        json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
