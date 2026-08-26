# Public/private boundary

## Public and committed

- connector source and CLI;
- JSON schemas;
- compact result and receipt shapes;
- install and mechanical validation gates;
- generic documentation.

## Private and never committed

- search and selected-job artifacts;
- their runtime receipts;
- transaction drafts;
- transaction manifests and claims;
- classification attempt reservations and receipts;
- the jobs database and state root;
- raw job titles, companies, locations, descriptions, URLs, and account state;
- personal profiles, preferences, filtering policy, classifier source, terminal classification values, scores, and applications.

The connector receives all paths from a trusted parent process. It has no default private root or database path. Transaction references are canonical relative paths beneath the configured private root. Raw values are written only to the private database; stdout and the immutable connector receipt contain counts and SHA-256 digests.

The preparer receives the private draft by path; draft field values never appear on its command line or stdout. Its output contains only public-safe seat/correlation identity, digests, modes, counts, and boolean preflight evidence. A refusal marker contains only those public identities, a fixed schema/state, and a failure code.

The database must already exist, be an owner-controlled regular file with mode `0600`, and contain the required tables and columns. The connector never initializes or migrates it.

The classification claim contains only private relative references, SHA-256 bindings, and one parent-authorized terminal enum. It contains no raw job field or policy rule. The claim path, database path, and expected claim digest are parent-bound runtime arguments; the verdict and all policy values remain absent from the command line and model request. The compact result omits the job identity, verdict, and policy/classifier digests. The immutable receipt exposes only public-safe digests, counts, and fixed postcondition labels.

## Taey-facing contract

The model-facing intake and classification tools accept `{}`. A parent runtime binds the active seat and correlation identity to one immutable private transaction and invokes the selected CLI once. An accepted classification claim reserves a digest-derived immutable attempt marker before the database transition; the same claim is never replayed. Any later recovery requires a separately governed new claim after root-cause reconciliation, never an automatic retry.
