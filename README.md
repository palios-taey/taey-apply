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
taey-linkedin-intake --help
```

There are no runtime dependencies outside the Python standard library.

## Parent-side invocation

A trusted parent runtime creates a canonical private transaction, keeps every referenced artifact beneath an owner-controlled `0700` private root, and invokes the CLI once. Paths are mandatory parent-side arguments; there are no machine-specific defaults.

```bash
taey-linkedin-intake \
  --private-root "$TAEY_APPLY_PRIVATE_ROOT" \
  --database "$TAEY_APPLY_DB" \
  --transaction-file "$TAEY_APPLY_PRIVATE_ROOT/transactions/SEAT/CORRELATION.json" \
  --expected-transaction-sha256 64HEX \
  --receipt-file "$TAEY_APPLY_PRIVATE_ROOT/receipts/SEAT/CORRELATION.json" \
  --requester PUBLIC_SAFE_SEAT \
  --turn-id PUBLIC_SAFE_TURN \
  --correlation-id PUBLIC_SAFE_CORRELATION \
  --process-generation 32HEX
```

The model-facing tool should have an empty object schema. The parent resolves the frozen private transaction from seat and correlation lineage; Taey never supplies paths, card names, company names, titles, descriptions, or job IDs.

See [Private boundary](docs/PRIVATE_BOUNDARY.md), [Architecture](docs/ARCHITECTURE.md), and the versioned JSON schemas in [`schemas/`](schemas/).

## Mechanical gates

```bash
python3 tools/check_public_boundary.py
python3 -m pip install .
python3 tools/validate_contract.py
```

These gates validate packaging and the deterministic data boundary with generated sanitized state. They are not a production qualification. Production use remains blocked until exact-SHA review, merge, deployment, and a real Taey execution receipt.

## Status

Version `0.1.0` implements capture-to-unclassified-intake only. Classification, scoring, ATS interaction, and application actions are deliberately absent.
