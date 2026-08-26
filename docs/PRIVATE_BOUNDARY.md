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
- transaction manifests and claims;
- the jobs database and state root;
- raw job titles, companies, locations, descriptions, URLs, and account state;
- personal profiles, preferences, filtering policy, scores, and applications.

The connector receives all paths from a trusted parent process. It has no default private root or database path. Transaction references are canonical relative paths beneath the configured private root. Raw values are written only to the private database; stdout and the immutable connector receipt contain counts and SHA-256 digests.

The database must already exist, be an owner-controlled regular file with mode `0600`, and contain the required tables and columns. The connector never initializes or migrates it.

## Taey-facing contract

The eventual model-facing tool accepts `{}`. A parent runtime binds the active seat and correlation identity to one immutable private transaction and invokes this CLI once. A completed identity is never retried; deduplication is demonstrated with a distinct transaction identity referencing the same source evidence.
