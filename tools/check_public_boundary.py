#!/usr/bin/env python3
from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {"", ".md", ".py", ".toml", ".yml", ".yaml", ".json", ".example"}
FORBIDDEN_TEXT = {
    "operator_home": re.compile(r"/(?:home|Users)/[A-Za-z0-9._-]+/"),
    "private_ipv4": re.compile(
        r"\b(?:10(?:\.\d{1,3}){3}|127\.0\.0\.1|192\.168(?:\.\d{1,3}){2}|"
        r"172\.(?:1[6-9]|2[0-9]|3[01])(?:\.\d{1,3}){2})\b"
    ),
    "credential_assignment": re.compile(
        r"(?i)\b(?:password|passwd|api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret)\s*[:=]\s*['\"][^'\"]+"
    ),
    "credential_literal": re.compile(r"\b(?:gh[pousr]_|sk-)[A-Za-z0-9_-]{12,}"),
    "private_repo_pointer": re.compile(
        r"\b(?:" + "apply" + r"-machine|" + "infra" + r"-soul)\b"
    ),
    "silent_check_false": re.compile(
        r"subprocess\.(?:run|call|Popen)\([^\n]*check\s*=\s*False"
    ),
}
FORBIDDEN_IMPORT_ROOTS = {
    "agent_worker",
    "ats_mcp_server",
    "boards",
    "filter",
    "instructions",
    "prescore",
    "score",
    "submit_receipt",
    "taey_submit",
    "targets",
}
REQUIRED_FILES = {
    ".env.example",
    ".gitignore",
    ".github/workflows/ci.yml",
    "AGENTS.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "KNOWN_FINDINGS.md",
    "README.md",
    "SECURITY.md",
    "docs/ARCHITECTURE.md",
    "docs/AUTONOMOUS_APPLICATION_RUNBOOK.md",
    "docs/LINKEDIN_APPLICATION_CLASSIFICATION_RUNBOOK.md",
    "docs/LINKEDIN_APPLICATION_INTAKE_RUNBOOK.md",
    "docs/PRIVATE_BOUNDARY.md",
    "pyproject.toml",
    "schemas/application-envelope-v1.json",
    "schemas/application-context-v1.json",
    "schemas/application-gate-receipt-v1.json",
    "schemas/employer-confirmation-v1.json",
    "schemas/application-lifecycle-v1.json",
    "schemas/application-materialization-manifest-v1.json",
    "schemas/application-receipt-v1.json",
    "schemas/application-result-v1.json",
    "schemas/linkedin-intake-private-input-v1.json",
    "schemas/linkedin-intake-receipt-v1.json",
    "schemas/linkedin-intake-result-v1.json",
    "schemas/linkedin-classification-private-claim-v1.json",
    "schemas/linkedin-classification-preparation-manifest-v1.json",
    "schemas/linkedin-classification-receipt-v1.json",
    "schemas/linkedin-classification-result-v1.json",
    "src/taey_apply/classification_cli.py",
    "src/taey_apply/application_confirmation.py",
    "src/taey_apply/application_contract.py",
    "src/taey_apply/application_materialize_cli.py",
    "src/taey_apply/application_materializer.py",
    "src/taey_apply/application_prepare_cli.py",
    "src/taey_apply/application_preparer.py",
    "src/taey_apply/application_runner.py",
    "src/taey_apply/classification_prepare_cli.py",
    "src/taey_apply/classification_preparer.py",
    "src/taey_apply/classification_contract.py",
    "src/taey_apply/cli.py",
    "src/taey_apply/contract.py",
    "src/taey_apply/linkedin_intake.py",
    "src/taey_apply/linkedin_classification.py",
    "src/taey_apply/prepare_cli.py",
    "src/taey_apply/preparer.py",
    "tools/validate_preparer.py",
    "tools/validate_application_boundary.py",
    "tools/validate_application_materializer.py",
    "tools/validate_classification.py",
    "tools/validate_classification_preparer.py",
}


def repository_files() -> list[Path]:
    output = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
    )
    relative_paths = output.decode("utf-8").split("\0")
    return sorted(
        ROOT / relative
        for relative in relative_paths
        if relative and (ROOT / relative).suffix in TEXT_SUFFIXES
    )


def scan_text(paths: list[Path]) -> list[str]:
    findings: list[str] = []
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        for label, pattern in FORBIDDEN_TEXT.items():
            if pattern.search(text):
                findings.append(f"{relative}: {label}")
    return findings


def scan_python(paths: list[Path]) -> list[str]:
    findings: list[str] = []
    for path in (item for item in paths if item.suffix == ".py"):
        relative = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".", 1)[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots = {node.module.split(".", 1)[0]}
            else:
                roots = set()
            forbidden = roots & FORBIDDEN_IMPORT_ROOTS
            if forbidden:
                findings.append(
                    f"{relative}:{node.lineno}: forbidden import {sorted(forbidden)}"
                )
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                findings.append(f"{relative}:{node.lineno}: bare except")
            if (
                isinstance(node, ast.ExceptHandler)
                and isinstance(node.type, ast.Name)
                and node.type.id == "Exception"
            ):
                boundary = next(
                    (
                        function
                        for function in ast.walk(tree)
                        if isinstance(function, ast.FunctionDef)
                        and function.name == "run_application"
                        and function.lineno <= node.lineno <= function.end_lineno
                    ),
                    None,
                )
                if not (
                    relative == "src/taey_apply/application_runner.py"
                    and boundary is not None
                ):
                    findings.append(
                        f"{relative}:{node.lineno}: broad Exception handler"
                    )
    return findings


def main() -> int:
    paths = repository_files()
    relative_paths = {path.relative_to(ROOT).as_posix() for path in paths}
    findings = [
        f"missing required file: {path}"
        for path in sorted(REQUIRED_FILES - relative_paths)
    ]
    findings.extend(scan_text(paths))
    findings.extend(scan_python(paths))
    for schema_path in ROOT.joinpath("schemas").glob("*.json"):
        json.loads(schema_path.read_text(encoding="utf-8"))
    ignored = subprocess.call(["git", "check-ignore", "-q", ".env"], cwd=ROOT) == 0
    if not ignored:
        findings.append(".env is not ignored")
    result = {
        "schema": "taey_apply_public_boundary_gate_v1",
        "files_scanned": len(paths),
        "findings": findings,
        "verdict": "PASS" if not findings else "FAIL",
    }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
