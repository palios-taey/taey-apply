# taey-apply

`taey-apply` is the public application-domain intake boundary used by Taey. Its first connector accepts one frozen, privately stored LinkedIn search-card capture paired with the exact selected-job capture that followed it, validates their immutable receipts, derives a stable LinkedIn job identity, and inserts one unclassified row into an existing private jobs database.

It does not search LinkedIn, drive a browser, score a person-job fit, read a personal profile, select a job, or apply to anything.

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

The connector recognizes the public receipt/artifact contracts emitted by `palios-taey/taeys-hands`. Raw captures, account data, database paths, and personal policy remain outside this repository and outside model context.

## Install

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
taey-prepare-linkedin-intake --help
taey-linkedin-intake --help
```

There are no runtime dependencies outside the Python standard library.

## Canonical production sequence

A trusted parent writes the seven private transaction fields to an owner-controlled `0400` draft beneath the configured `0700` private root. It never hand-writes the final transaction. `taey-prepare-linkedin-intake` reserves the fresh identity, validates the draft and exact four-source pairing, writes canonical no-newline bytes once, freezes the transaction `0400`, and returns only compact preflight evidence. A refusal after root and identity acceptance writes an immutable terminal marker so the identity cannot be retried through preparation or Presence.

```bash
taey-prepare-linkedin-intake \
  --private-root "$TAEY_APPLY_PRIVATE_ROOT" \
  --draft-file "$TAEY_APPLY_DRAFT_FILE" \
  --seat-id "$TAEY_APPLY_SEAT_ID" \
  --correlation-id "$TAEY_APPLY_CORRELATION_ID"
```

Verify the compact result, then invoke Taey's empty-object `linkedin_application_intake` tool exactly once through the configured Presence proxy. A failed, claimed, or completed identity is never retried. The full commands and postconditions live only in the [canonical LinkedIn application-intake runbook](docs/LINKEDIN_APPLICATION_INTAKE_RUNBOOK.md).

Taey never supplies paths, card names, company names, titles, descriptions, or job IDs. See [Private boundary](docs/PRIVATE_BOUNDARY.md), [Architecture](docs/ARCHITECTURE.md), and the versioned JSON schemas in [`schemas/`](schemas/).

## Mechanical gates

```bash
python3 tools/check_public_boundary.py
python3 -m pip install .
python3 tools/validate_preparer.py
python3 tools/validate_contract.py
```

These gates validate packaging and the deterministic data boundary with generated sanitized state. They are not a production qualification. Production use remains blocked until exact-SHA review, merge, deployment, and a real Taey execution receipt.

## Status

Version `0.1.1` implements deterministic transaction preparation and capture-to-unclassified-intake only. Classification, scoring, ATS interaction, and application actions are deliberately absent.
