# Architecture

## Responsibility boundary

| Owner | Responsibility |
|---|---|
| Parent runtime | Seat/correlation lineage, private paths, single-use claim, frozen card choice |
| Capture producer | Exact UI observation/action receipts and immutable private artifacts |
| Transaction preparer | Validate an owner-private draft and exact source pairing, then create one canonical unclaimed identity |
| Classification claim preparer | Validate one manifest-bound private policy decision against one pristine qualified row and publish one existing-schema claim or refusal |
| `taey-apply` | Validate the paired evidence, derive canonical identity, deduplicate, persist unclassified intake, return a compact receipt |
| Application preparer | Validate six immutable prerequisite gates and publish one single-use provider-neutral envelope |
| Application materializer | Compile completed private evidence into six gates, one opaque context, and one lifecycle |
| Application runner | Enforce one-action receipt chaining and terminate only at exact employer confirmation or one of five declared stop boundaries |
| Runtime action compiler | Validate one Taey decision against the current bounded surface, copy exact private values/artifacts from the immutable context, and freeze one Presence-owned action |
| Injected one-action executor | Stateful public adapter; owns one schema decision and one reviewed Presence transaction per runner call, but no UI or lifecycle policy |
| Presence / Hands | Provider-specific UI mapping, exact one-action execution, and postcondition evidence |

`taey-apply` owns no browser or platform UI behavior. It treats captures as signed-by-digest inputs and stops if their relationship is not exact.

## Autonomous application boundary

The additive application layer does not add another UI driver. A trusted parent
freezes six exact, privacy-safe gate receipts for discovery, qualification, deep
research, materials, truthful applicant data, and submission authority, plus the
digest of one opaque private application context. The preparer verifies every
receipt against the same application identity and writes one canonical `0400`
envelope.

The runner reserves the envelope digest before invoking any executor. It calls
an injected `OneActionExecutor` protocol with only the frozen envelope identity,
sequence number, and prior receipt digest. Every accepted outcome proves either
one read-only observation, one mutation plus its exact postcondition, or exact
employer confirmation. No later call is made unless the prior outcome explicitly
authorizes it. A duplicate action/receipt, malformed outcome, or executor error
is terminal side-effect uncertainty.

Success requires a stable exact confirmation binding: matching provider and
application identity, one exact route digest, one exact confirmation-anchor
digest, at least two consecutive matched samples of one stable surface revision,
the samples digest, and the exact terminal executor receipt. Human review,
approval fields, and review queues are absent. The bounded action budget is an
authority boundary, not permission to retry.

The runtime compiler is additive and provider-specific. Its initial call emits
only `observe_form`. Every later call requires one exact current
`ats_greenhouse_next_action_surface_v1` capsule, one exact Taey decision, the
prior Hands event hash, and the immutable application context. Taey supplies
only an allowed operation, the current revision, fact/evidence keys, and the
current ref for non-option actions. For a fresh options capsule, ref and option
name remain null and the private compiler resolves one exact match. Taey never
supplies an applicant value, artifact path, locator, or primitive. The compiler
verifies the selected keys, copies the private bytes from the context, and
writes one owner-controlled `0400` Hands action plus the exact Presence manifest
for that turn.

The model-facing action schema contains only operations evidenced by the current
surface. Form operations retain canonical control order and are deduplicated;
native-dialog authority is the single next step in the frozen chooser sequence.
`halt` remains available, while `select_option` is exposed only by a fresh
options capsule.

The compiler preserves the one-action native chooser sequence. An exact
`Country` / `combo box` origin must carry one nonempty unique public
`semantic_token` for every current option. The compiler matches the immutable
private fact to exactly one token, then freezes that control's untouched
rendered `name` for selection postvalidation. Every other options origin must
carry no semantic token and continues to require exact rendered-name equality.
Taey receives the fact key, not its value, and supplies neither an option ref
nor an option name. An unsupported question, absent match, duplicate match, or
action stops as `unmapped_ui_or_question`; an invalid capsule, missing or
duplicate Country token, or token on another origin stops as
`exact_postcondition_failure`; an absent exact value or artifact stops as
`missing_truthful_applicant_data`. There is no prefix, fuzzy, or normalized
fallback and no human review, approval, queue, automatic retry, or generic
field map.

The reviewed Presence `greenhouse-ats-ui` profile owns the raw
`POST /v1/greenhouse-ats/one-action` transaction route; Hands
`ff01cc29290e31a28699104ef8e67013572813da` owns execution. Production must pin
an exact reviewed Presence checkout that exposes that contract. The direct
route returns the raw validated terminal tool object and stops after the first
refusal. A separately reviewed injected executor may bind that route to the
provider-neutral runner. The generic chat-completions response is
model-authored final text and is not accepted as a machine receipt. The runner
and compiler do not parse model prose, shell into Hands, or read a hidden
receipt path.

`GreenhousePresenceOneActionExecutor` is the concrete binding. Its initial
runner call compiles only `observe_form`; every later call gives Taey the exact
bounded capsule, the prior action kind, and only the available fact/evidence
key names. Native vLLM `json_schema` generation runs with thinking disabled and
no tools. The adapter validates the exact decision, publishes one compiler
action/manifest, and makes one POST to the reviewed Presence route with exact
seat/event/correlation/profile headers and a display-only body. It validates
the echoed response lineage and converts the bounded receipt, next capsule, or
employer confirmation directly into `OneActionOutcome`.

Decision failure occurs before action publication. Compiler failure occurs
before Presence. A read-only observation refusal becomes one terminal halt. A
missing or malformed response after a possible mutation is side-effect
uncertainty, and the executor permanently refuses any later call. There is no
human-review state, approval field, queue, automatic retry, model-prose parser,
direct Hands import, or provider-field guess.

Every direct-vLLM response is frozen once as canonical `0400` private evidence
immediately after the bounded HTTP response and before decision validation. The
artifact binds seat/event/correlation lineage, request and response digests,
and the full parsed response payload. It is separate from accepted decisions.
Malformed envelopes, content, fields, and cross-field authority are finite
machine classifications; none authorizes Presence or a retry.

Every returned terminal executor outcome is the digest of one canonical `0400`
private evidence receipt, not an in-memory object that is discarded. The
receipt carries a fixed stage/reason pair, the finite decision-rejection code,
lineage digests, exact private response ref/digest, optional capsule/Presence
payload digests, and a digest/private ref to one accepted bounded Taey decision
when such a decision existed. Explicit Taey `halt` is distinct from compiler
refusal. The runner reopens and validates all referenced artifacts before
accepting the outcome and binds the terminal evidence ref and digest into the
final private result.

## Application materialization boundary

The parent-only materializer accepts one canonical owner-controlled `0400`
manifest beneath the private root. The manifest binds completed discovery,
qualification, deep-research, and materials artifacts by relative reference,
content digest, kind, media type, and terminal state. The same immutable
manifest carries canonical applicant facts, work evidence, and submission
policy under the application identity, eliminating three intermediate files.

The materializer reads every source without mutation, requires exact owner-only
mode and digest, and publishes six exact `0400` gate receipts, one canonical
opaque context, and one lifecycle that the existing application preparer can
consume unchanged. The context preserves private fact and evidence values so a
later Taey step can compare a newly observed live question or option set against
truthful evidence. It never selects a dynamic option itself. An absent required
fact or work claim stops as `missing_truthful_applicant_data`; absent submission
authority stops as `policy_or_authority_boundary`.

The materializer owns no browser, ATS provider grammar, database, submission,
human-review state, approval field, or review queue. A live question that has no
exact answer in the frozen context remains a downstream terminal
`missing_truthful_applicant_data` event, not permission to guess.

## Greenhouse runtime compiler boundary

`GreenhouseActionCompiler` consumes the materialized context unchanged. It does
not prebind a company form label because live Greenhouse forms and dropdown
options vary. Taey examines only the bounded public capsule and returns a
decision matching `schemas/greenhouse-action-decision-v1.json`. The compiler
then verifies that the ref occurs exactly once at the cited revision and that
the requested operation is currently declared by Hands.

Text and option actions require one existing `fact_key`; selected work evidence
keys must also exist but never become answer text. A fill fact must already be
exact rendered text. A dropdown fact must equal one fresh rendered name except
for the exact Country semantic-token contract described above. Boolean choice
activation requires exact `true`. Artifact actions
derive the sole matching resume or cover from the materialized materials stage
and revalidate its immutable bytes before placing its absolute path only in the
private frozen action.

Submit compilation additionally requires
`surface_capsule.required_controls_complete == true`. Hands
`ff01cc29290e31a28699104ef8e67013572813da` emits that Boolean from its exact
required-control proof, and the reviewed Presence contract validates and relays
it. The compiler does not infer completeness from optional fields or from
action history.

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

The parent-only classification preparer removes hand construction without widening Taey's authority. One canonical `0400` manifest pins the qualified intake refs/hashes, private policy artifact, exact classifier bytes, canonical priority-board artifact, and fresh claim/refusal identities. The preparer opens the `0600` database read-only with `query_only`, reuses the existing qualified-intake and row-digest functions, requires one exact pristine row, computes the exact policy-input algebra, and invokes the classifier once. A classifier-local import adapter resolves only the exact absolute `boards` module to a deep copy of the pinned priority-board list, delegates every other import to Python, and never changes `sys.modules` or search paths. Only `PASS` or `KILLED` can enter the existing claim schema. Claim publication is exclusive, immutable, fsynced, and read back; successful preparation does not reserve the commit attempt.

A failure after output-identity acceptance publishes only the distinct immutable refusal marker unless a claim publication already exists or is indeterminate. Every new refusal includes one fixed non-sensitive preparation stage and a boolean stating whether classifier invocation was attempted; it includes no exception text, path, row field, decision detail, or traceback. The preparer performs no database mutation, scorer activation, UI, ATS, application, message, or outward action and is not exposed as a model-facing tool.

The private parent owns, reviews, and pins its existing personal policy outside this repository. The parent-only preparer invokes those exact bytes and freezes one owner-controlled `0400` claim. The claim binds the exact qualified intake transaction and receipt digests, the complete prewrite row digest, the stable row digest, the private policy/input digest, the private classifier digest, and one terminal `PASS` or `KILLED` decision.

The public connector reconstructs the canonical URL only from the existing intake transaction and its four source artifacts. It validates the complete upstream intake receipt against that reconstruction and never accepts a raw URL or job identity on the command line. A fixed `classification-attempts/TRANSACTION_SHA.json` reservation makes an accepted claim protocol-nonreplayable before database mutation.

Inside `BEGIN IMMEDIATE`, the connector requires one exact URL row with `verdict`, `score`, and `applied_at` all SQL `NULL`; verifies complete and stable row digests; updates only `verdict`; and proves that the `jobs`, `applications`, and `apply_runs` counts and every non-verdict target column remain unchanged. A committed readback precedes receipt publication. The compact result omits the terminal decision, job identity, and policy/classifier digests. The immutable receipt contains digests and counts but no raw private values.

The connector does not validate the semantic correctness of the parent-owned policy decision. It validates only the immutable decision binding and its exact one-row persistence.

Cross-resource states are not described as atomic. A database commit that cannot be proven is `WRITE_INDETERMINATE`; a committed row that cannot be proven is `SIDE_EFFECT_UNCERTAIN`; and a proven commit whose receipt cannot be proven is `RECEIPT_INDETERMINATE`. All are terminal and grant no retry.

## Failure model

Every zero/duplicate match, noncanonical job identity, digest mismatch, receipt mismatch, unsafe path, unexpected database schema, write trigger, stored-row conflict, replay, or database error stops the call. There are no alternate selectors, schema coercions, automatic retries, deletes, or fallbacks. The only classification update is the exact single-column transition described above.
