# Architecture

## Responsibility boundary

| Owner | Responsibility |
|---|---|
| Parent runtime | Seat/correlation lineage, private paths, single-use claim, frozen card choice |
| Capture producer | Exact UI observation/action receipts and immutable private artifacts |
| Transaction preparer | Validate an owner-private draft and exact source pairing, then create one canonical unclaimed identity |
| Classification claim preparer | Validate one manifest-bound private policy decision against one pristine qualified row and publish one existing-schema claim or refusal |
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

New rows have `verdict`, `score`, and `applied_at` set to SQL `NULL`. A scorer that claims only explicit `PASS` rows cannot see them. Classification remains a separate transaction because it depends on user policy; folding it into intake would silently expand authority.

## Classification commit boundary

The parent-only classification preparer removes hand construction without widening Taey's authority. One canonical `0400` manifest pins the qualified intake refs/hashes, private policy artifact, exact classifier bytes, canonical priority-board artifact, and fresh claim/refusal identities. The preparer opens the `0600` database read-only with `query_only`, reuses the existing qualified-intake and row-digest functions, requires one exact pristine row, computes the exact policy-input algebra, and invokes the classifier once. Only `PASS` or `KILLED` can enter the existing claim schema. Claim publication is exclusive, immutable, fsynced, and read back; successful preparation does not reserve the commit attempt.

A failure after output-identity acceptance publishes only the distinct immutable refusal marker unless a claim publication already exists or is indeterminate. The preparer performs no database mutation, scorer activation, UI, ATS, application, message, or outward action and is not exposed as a model-facing tool.

The private parent owns, reviews, and pins its existing personal policy outside this repository. The parent-only preparer invokes those exact bytes and freezes one owner-controlled `0400` claim. The claim binds the exact qualified intake transaction and receipt digests, the complete prewrite row digest, the stable row digest, the private policy/input digest, the private classifier digest, and one terminal `PASS` or `KILLED` decision.

The public connector reconstructs the canonical URL only from the existing intake transaction and its four source artifacts. It validates the complete upstream intake receipt against that reconstruction and never accepts a raw URL or job identity on the command line. A fixed `classification-attempts/TRANSACTION_SHA.json` reservation makes an accepted claim protocol-nonreplayable before database mutation.

Inside `BEGIN IMMEDIATE`, the connector requires one exact URL row with `verdict`, `score`, and `applied_at` all SQL `NULL`; verifies complete and stable row digests; updates only `verdict`; and proves that the `jobs`, `applications`, and `apply_runs` counts and every non-verdict target column remain unchanged. A committed readback precedes receipt publication. The compact result omits the terminal decision, job identity, and policy/classifier digests. The immutable receipt contains digests and counts but no raw private values.

The connector does not validate the semantic correctness of the parent-owned policy decision. It validates only the immutable decision binding and its exact one-row persistence.

Cross-resource states are not described as atomic. A database commit that cannot be proven is `WRITE_INDETERMINATE`; a committed row that cannot be proven is `SIDE_EFFECT_UNCERTAIN`; and a proven commit whose receipt cannot be proven is `RECEIPT_INDETERMINATE`. All are terminal and grant no retry.

## Failure model

Every zero/duplicate match, noncanonical job identity, digest mismatch, receipt mismatch, unsafe path, unexpected database schema, write trigger, stored-row conflict, replay, or database error stops the call. There are no alternate selectors, schema coercions, automatic retries, deletes, or fallbacks. The only classification update is the exact single-column transition described above.
