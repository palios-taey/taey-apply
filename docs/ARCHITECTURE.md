# Architecture

## Responsibility boundary

| Owner | Responsibility |
|---|---|
| Parent runtime | Seat/correlation lineage, private paths, single-use claim, frozen card choice |
| Capture producer | Exact UI observation/action receipts and immutable private artifacts |
| Transaction preparer | Validate an owner-private draft and exact source pairing, then create one canonical unclaimed identity |
| `taey-apply` | Validate the paired evidence, derive canonical identity, deduplicate, persist unclassified intake, return a compact receipt |
| Later policy stage | Decide `PASS` or `KILLED` and authorize scoring |
| Later application stage | Any ATS or application action under its own authority |

`taey-apply` owns no browser or platform UI behavior. It treats captures as signed-by-digest inputs and stops if their relationship is not exact.

## Preparation boundary

The draft is private input, not mutation authority. The preparer accepts only its path plus public-safe seat and correlation identities. After validating the private root and identities, it reserves fresh `transactions/SEAT`, `claims/SEAT`, and `receipts/SEAT` parents at `0700`. It validates the same transaction fields and `load_linkedin_capture` pairing used by intake, writes canonical JSON bytes without a trailing newline to the final transaction with `O_EXCL`, fsyncs, freezes the file at `0400`, and verifies exact readback. It never opens a database or creates an intake claim.

Any draft, pairing, or readback refusal after reservation creates an immutable `0400` terminal marker at the derived receipt path. Presence checks receipt existence before loading a transaction, so it refuses the marked identity; the existing seat parents also make later preparation refuse it. An existing identity or any indeterminate write is terminal. The preparer never deletes partial evidence and never repairs or retries an identity.

## Data flow

1. Validate the canonical private transaction and its parent-claimed SHA-256.
2. Read four owner-private, nonsymlink, immutable files beneath the configured private root.
3. Validate exact schemas, key sets, terminal states, locks, postconditions, and content digests.
4. Require one card digest and bind its target/title/company hashes to the selected-job receipt.
5. Require one shared search reference.
6. Parse exactly one numeric `currentJobId` from the selected capture URL and normalize it to `https://www.linkedin.com/jobs/view/ID/`.
7. Require the live-compatible declared column types, a `TEXT` URL sole primary key or single-column unique identity, and absence of write triggers on `jobs`.
8. Execute one `INSERT OR IGNORE` and derive `records_written` from SQLite's observed effect.
9. Select every exact URL match, require exactly one, compare all capture fields, and prove that application-state table counts did not change.
10. Commit, write one immutable private receipt, and emit a compact public result.

## Scoring boundary

New rows have `verdict`, `score`, and `applied_at` set to SQL `NULL`. A scorer that claims only explicit `PASS` rows cannot see them. Classification is a separate future transaction because it depends on user policy; folding it into intake would silently expand authority.

## Failure model

Every zero/duplicate match, noncanonical job identity, digest mismatch, receipt mismatch, unsafe path, unexpected database schema, write trigger, stored-row conflict, or database error stops the call. There are no alternate selectors, schema coercions, retries, updates, deletes, or fallbacks.
