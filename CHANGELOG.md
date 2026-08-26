# Changelog

## Unreleased

- Record two independently checked, one-call production intake proofs for the exact public and Presence commits, including exact pairing, unchanged application state, unclassified rows, and zero-turn cleanup.
- Add a deterministic private-draft preparer that reserves one identity, validates the existing source-pairing contract, writes canonical no-newline transaction bytes, and terminalizes every accepted-identity refusal against reuse.
- Add the canonical prepare, verify, and one-Taey-call production runbook.
- Require an exact `TEXT` URL primary key or single-column unique identity before intake.
- Derive the write count from SQLite and observe exactly one matching URL row before success.
- Reject noncanonical job IDs and source receipts with fewer than two required stable cycles.
- Add adversarial coverage for identity, pairing, lineage, digest, stabilization, and conflict failures.

## 0.1.0

- Add the deterministic LinkedIn capture-to-unclassified-intake connector.
- Add strict private-input, compact-result, and immutable-receipt schemas.
- Add clean-room install and public-boundary gates.
