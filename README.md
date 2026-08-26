# taey-apply

`taey-apply` is the public application-domain state-transition boundary used by Taey. Its intake connector accepts one frozen, privately stored LinkedIn search-card capture paired with the exact selected-job capture that followed it, validates their immutable receipts, derives a stable LinkedIn job identity, and inserts one unclassified row into an existing private jobs database. Its separate classification connector applies one private-parent decision capsule to that exact qualified row.

It does not search LinkedIn, drive a browser, evaluate personal policy, score a person-job fit, read a personal profile, select a job, or apply to anything.

## Current capability

```text
private mounted-card artifact + receipt
private selected-job artifact + receipt
                 |
                 v
strict pairing and digest validation
                 |
                 v
INSERT OR IGNORE one complete jobs row
verdict = NULL, score = NULL, applied_at = NULL
                 |
                 v
compact immutable receipt; no raw values on stdout
```

The separate classification transition is:

```text
private immutable parent decision claim
+ exact qualified intake receipt digest
                 |
                 v
derive the existing row from frozen intake evidence
require verdict/score/applied_at all SQL NULL
                 |
                 v
UPDATE exactly one verdict to PASS or KILLED
score/applied_at stay NULL; application tables unchanged
                 |
                 v
privacy-safe immutable receipt; verdict omitted from stdout
```

The private parent owns policy evaluation and claim construction. The public connector proves claim-to-row persistence, not the semantic correctness of the private policy decision.

The connector recognizes the public receipt/artifact contracts emitted by `palios-taey/taeys-hands`. Raw captures, account data, database paths, and personal policy remain outside this repository and outside model context.

## Install

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
python -P -m taey_apply.prepare_cli --help
python -P -m taey_apply.cli --help
python -P -m taey_apply.classification_cli --help
```

There are no runtime dependencies outside the Python standard library.

## Canonical production sequence

A trusted parent writes the seven private transaction fields to an owner-controlled `0400` draft beneath the configured `0700` private root. It never hand-writes the final transaction. The preparer reserves the fresh identity, validates the draft and exact four-source pairing, writes canonical no-newline bytes once, freezes the transaction `0400`, and returns only compact preflight evidence. A refusal after root and identity acceptance writes an immutable terminal marker so the identity cannot be retried through preparation or Presence.

Production does not depend on an installed console command, an activated environment, `PATH`, or the current directory. `TAEY_APPLY_PUBLIC_ROOT` is the canonical absolute path to the exact reviewed public checkout, and `TAEY_APPLY_PYTHON` is the canonical absolute path to the explicit Python interpreter used by Presence.

```bash
PYTHONPATH="$TAEY_APPLY_PUBLIC_ROOT/src" \
  "$TAEY_APPLY_PYTHON" -P -m taey_apply.prepare_cli \
  --private-root "$TAEY_APPLY_PRIVATE_ROOT" \
  --draft-file "$TAEY_APPLY_DRAFT_FILE" \
  --seat-id "$TAEY_APPLY_SEAT_ID" \
  --correlation-id "$TAEY_APPLY_CORRELATION_ID"
```

Verify the compact result, then invoke Taey's empty-object `linkedin_application_intake` tool exactly once through the configured Presence proxy. A failed, claimed, or completed identity is never retried. The full commands and postconditions live only in the [canonical LinkedIn application-intake runbook](docs/LINKEDIN_APPLICATION_INTAKE_RUNBOOK.md).

Taey never supplies paths, card names, company names, titles, descriptions, job IDs, verdicts, or policy values. The classification invocation uses a frozen claim path and digest bound by its trusted parent runtime. See [Private boundary](docs/PRIVATE_BOUNDARY.md), [Architecture](docs/ARCHITECTURE.md), the [classification runbook](docs/LINKEDIN_APPLICATION_CLASSIFICATION_RUNBOOK.md), and the versioned JSON schemas in [`schemas/`](schemas/).

## Mechanical gates

```bash
python3 tools/check_public_boundary.py
python3 -m pip install .
python3 tools/validate_preparer.py
python3 tools/validate_contract.py
python3 tools/validate_classification.py
```

These gates validate packaging and the deterministic data boundary with generated sanitized state. They do not replace exact-SHA review, deployment, or real Taey execution receipts. The production evidence qualified for the current baseline is recorded in the [canonical runbook](docs/LINKEDIN_APPLICATION_INTAKE_RUNBOOK.md#qualified-production-baseline).

## Status

Version `0.1.1` includes deterministic transaction preparation, capture-to-unclassified intake, and the additive classification commit connector. Public commit `253b882571673ae30d3beadda6f174439755a241`, through Presence commit `c42bd319b2fb8ef6b9774b6ef171293baf73e897`, produced two independently checked production intake receipts against the active application feed. The classification connector is not production-qualified until the bounded runbook acceptance is completed. Policy evaluation, scoring, ATS interaction, and application actions remain absent.
