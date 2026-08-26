# Architecture

## Responsibility boundary

| Owner | Responsibility |
|---|---|
| Parent runtime | Seat/correlation lineage, private paths, single-use claim, frozen card choice |
| Capture producer | Exact UI observation/action receipts and immutable private artifacts |
| `taey-apply` | Validate the paired evidence, derive canonical identity, deduplicate, persist unclassified intake, return a compact receipt |
| Later policy stage | Decide `PASS` or `KILLED` and authorize scoring |
| Later application stage | Any ATS or application action under its own authority |

`taey-apply` owns no browser or platform UI behavior. It treats captures as signed-by-digest inputs and stops if their relationship is not exact.

## Data flow

1. Validate the canonical private transaction and its parent-claimed SHA-256.
2. Read four owner-private, nonsymlink, immutable files beneath the configured private root.
3. Validate exact schemas, key sets, terminal states, locks, postconditions, and content digests.
4. Require one card digest and bind its target/title/company hashes to the selected-job receipt.
5. Require one shared search reference.
6. Parse exactly one numeric `currentJobId` from the selected capture URL and normalize it to `https://www.linkedin.com/jobs/view/ID/`.
7. Require the existing private database schema and absence of write triggers on `jobs`.
8. Execute one `INSERT OR IGNORE`. A collision is accepted only when the stored capture fields match exactly.
9. Verify the row and prove that application-state table counts did not change.
10. Commit, write one immutable private receipt, and emit a compact public result.

## Scoring boundary

New rows have `verdict`, `score`, and `applied_at` set to SQL `NULL`. A scorer that claims only explicit `PASS` rows cannot see them. Classification is a separate future transaction because it depends on user policy; folding it into intake would silently expand authority.

## Failure model

Every zero/duplicate match, digest mismatch, receipt mismatch, unsafe path, unexpected database schema, write trigger, stored-row conflict, or database error stops the call. There are no alternate selectors, schema coercions, retries, updates, deletes, or fallbacks.
