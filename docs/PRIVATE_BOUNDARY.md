# Public/private boundary

## Public and committed

- connector source and CLI;
- JSON schemas;
- provider-neutral autonomous lifecycle, envelope, runner, and confirmation contracts;
- Greenhouse bounded-surface decision and runtime compiler contracts;
- compact result and receipt shapes;
- install and mechanical validation gates;
- generic documentation.

## Private and never committed

- search and selected-job artifacts;
- their runtime receipts;
- transaction drafts;
- transaction manifests and claims;
- classification preparation manifests, policy/classifier artifacts, refusal markers, and claims;
- classification attempt reservations and receipts;
- the jobs database and state root;
- application prerequisite evidence receipts and opaque application context;
- application materialization manifests, source artifacts, refusals, and
  lifecycles;
- Greenhouse action decisions, bounded surface receipts, frozen actions, and
  Presence manifests;
- applicant facts, answers, resume/cover-letter paths, and employer-specific values;
- raw job titles, companies, locations, descriptions, URLs, and account state;
- personal profiles, preferences, filtering policy, classifier source, terminal classification values, scores, and applications.

The connector receives all paths from a trusted parent process. It has no default private root or database path. Transaction references are canonical relative paths beneath the configured private root. Raw values are written only to the private database; stdout and the immutable connector receipt contain counts and SHA-256 digests.

The preparer receives the private draft by path; draft field values never appear on its command line or stdout. Its output contains only public-safe seat/correlation identity, digests, modes, counts, and boolean preflight evidence. A refusal marker contains only those public identities, a fixed schema/state, and a failure code.

The database must already exist, be an owner-controlled regular file with mode `0600`, and contain the required tables and columns. The connector never initializes or migrates it.

The classification preparation manifest contains only private relative references and SHA-256 bindings. The parent-only preparer reads those artifacts beneath the private root, passes the complete private row only to the exact pinned classifier bytes, and emits fixed state plus digests. The classifier receives a deep-copied priority-board list through an exact classifier-local `boards` import without a process-global module or search-path change. The command line and stdout contain no raw job field, policy rule, classifier source, or verdict. On success it writes the existing classification claim schema; on a post-identity refusal it writes the separate immutable refusal marker containing only fixed state, a fixed preparation-stage enum, classifier-invocation status, and existing safe digests. It never writes exception text, paths, row fields, decision details, traceback, a classification-attempt marker, or a database value.

The classification claim contains only private relative references, SHA-256 bindings, and one parent-authorized terminal enum. It contains no raw job field or policy rule. The claim path, database path, and expected claim digest are parent-bound runtime arguments; the verdict and all policy values remain absent from the command line and model request. The compact commit result omits the job identity, verdict, and policy/classifier digests. The immutable receipt exposes only public-safe digests, counts, and fixed postcondition labels.

The autonomous lifecycle contains only private relative references, digests,
fixed gate states, a provider identity, a bounded one-action call budget, and
derived outcome references. The opaque application-context file may contain
private applicant values, but the public runner never interprets or emits them.
The eventual Presence adapter resolves those values outside model-visible output
under its own reviewed contract. Terminal executor evidence contains only
lineage, finite stage/reason/rejection enums, digests, private relative refs,
fixed state, and one of the five declared stop codes. The direct-vLLM response
is frozen before validation in a distinct owner-controlled `0400` artifact. Its
request was built only from the public surface capsule, application digest, and
fact/evidence key names; raw applicant values and paths never enter it. A
separately frozen accepted decision contains only the bounded decision schema's
operation, opaque ref/revision, fact/evidence key names, and stop code. None of
these artifacts contains exception text. The final private result binds the
exact terminal evidence ref and digest; compact stdout exposes no private ref
or response payload.

The application materializer reads one canonical `0400` manifest and only its
bound `0400` artifacts beneath the explicit private root. Raw applicant facts,
work claims, and policy directives live inside that private manifest and are
copied only into the opaque `0400` application context.
Its stdout and immutable refusal contain fixed states, counts, public IDs, and
digests only. The public schemas define structure but contain no applicant
value, path, employer value, or policy decision.

The Greenhouse runtime compiler receives a bounded surface capsule and a Taey
decision containing only current opaque refs, revisions, allowed operation,
fact keys, and work-evidence keys. The bounded public capsule may carry rendered
option names and the exact Country semantic tokens produced by Hands; the
decision carries neither. The compiler reopens the immutable context itself.
Raw fact values and artifact paths are
copied only into the owner-controlled `0400` Hands action; they are absent from
the decision, compiler result, and Presence manifest. The compiler performs no
network request, UI action, shell command, or direct Hands call.

## Taey-facing contract

The model-facing intake and classification tools accept `{}`. Classification preparation is not a Taey tool; a trusted parent invokes it from the pinned manifest and reviewed checkout. A parent runtime binds the active seat and correlation identity to one immutable private transaction and invokes the selected commit CLI once. An accepted classification claim reserves a digest-derived immutable attempt marker before the database transition; the same claim is never replayed. Any later recovery requires a separately governed new claim after root-cause reconciliation, never an automatic retry.
