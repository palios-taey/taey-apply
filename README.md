# taey-apply

`taey-apply` is the public application-domain state-transition boundary used by Taey. Its intake connector accepts one frozen, privately stored LinkedIn search-card capture paired with the exact selected-job capture that followed it, validates their immutable receipts, derives a stable LinkedIn job identity, and inserts one unclassified row into an existing private jobs database. Its separate classification connector applies one private-parent decision capsule to that exact qualified row. Its provider-neutral application layer verifies frozen discovery, qualification, deep-research, materials, truthful-data, and submission-authority evidence before coordinating an injected one-action executor to an exact employer confirmation.

It does not search LinkedIn, implement ATS locators, traverse an accessibility tree, contain UI primitives, implement personal policy, score a person-job fit, store applicant facts, or own a private jobs database. The optional parent-only classification preparer can invoke exact private classifier bytes pinned by a private manifest; no policy rule or personal value is implemented or stored here. Application UI behavior remains owned by Presence and Hands behind a separately reviewed one-action adapter.

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

The separate classification transition is:

```text
private immutable parent decision claim
+ exact qualified intake receipt digest
                 |
                 v
derive the existing row from frozen intake evidence
require verdict/score/applied_at all SQL NULL
                 |
                 v
UPDATE exactly one verdict to PASS or KILLED
score/applied_at stay NULL; application tables unchanged
                 |
                 v
privacy-safe immutable receipt; verdict omitted from stdout
```

The additive autonomous application boundary is:

```text
six immutable prerequisite gate receipts
+ one opaque private application-context digest
                 |
                 v
single-use frozen application envelope
                 |
                 v
injected one-action executor; at most one mutation per call
+ exact postcondition receipt chain
                 |
                 v
two independent exact employer-confirmation observations
+ terminal immutable result; no later mutation authority
```

The public runner contains no concrete transport endpoint or tool name. Its
`OneActionExecutor` protocol is the only integration seam. Production binding
is intentionally blocked until the separately reviewed public Presence
contract is merged.

The private parent owns, reviews, and pins its policy artifacts. The parent-only preparer constructs the claim mechanically; the separate commit connector proves claim-to-row persistence. Neither proves the semantic correctness of the private policy decision.

The connector recognizes the public receipt/artifact contracts emitted by `palios-taey/taeys-hands`. Raw captures, account data, database paths, and personal policy remain outside this repository and outside model context.

## Install

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
python -P -m taey_apply.prepare_cli --help
python -P -m taey_apply.classification_prepare_cli --help
python -P -m taey_apply.cli --help
python -P -m taey_apply.classification_cli --help
python -P -m taey_apply.application_prepare_cli --help
```

There are no runtime dependencies outside the Python standard library.

## Canonical production sequence

A trusted parent writes the seven private transaction fields to an owner-controlled `0400` draft beneath the configured `0700` private root. It never hand-writes the final transaction. The preparer reserves the fresh identity, validates the draft and exact four-source pairing, writes canonical no-newline bytes once, freezes the transaction `0400`, and returns only compact preflight evidence. A refusal after root and identity acceptance writes an immutable terminal marker so the identity cannot be retried through preparation or Presence.

Production does not depend on an installed console command, an activated environment, `PATH`, or the current directory. `TAEY_APPLY_PUBLIC_ROOT` is the canonical absolute path to the exact reviewed public checkout, and `TAEY_APPLY_PYTHON` is the canonical absolute path to the explicit Python interpreter used by Presence.

```bash
PYTHONPATH="$TAEY_APPLY_PUBLIC_ROOT/src" \
  "$TAEY_APPLY_PYTHON" -P -m taey_apply.prepare_cli \
  --private-root "$TAEY_APPLY_PRIVATE_ROOT" \
  --draft-file "$TAEY_APPLY_DRAFT_FILE" \
  --seat-id "$TAEY_APPLY_SEAT_ID" \
  --correlation-id "$TAEY_APPLY_CORRELATION_ID"
```

Verify the compact result, then invoke Taey's empty-object `linkedin_application_intake` tool exactly once through the configured Presence proxy. A failed, claimed, or completed identity is never retried. The full commands and postconditions live only in the [canonical LinkedIn application-intake runbook](docs/LINKEDIN_APPLICATION_INTAKE_RUNBOOK.md).

Classification claim construction is also mechanical. A trusted parent supplies one canonical `0400` manifest that binds the qualified intake transaction and receipt, the exact private policy, classifier, and priority-board artifacts, and fresh claim/refusal references. The parent-only preparer observes the database in SQLite read-only/query-only mode, resolves the classifier's exact absolute `boards` import from a classifier-local copy of the pinned priority-board artifact, invokes the pinned classifier bytes exactly once, accepts only `PASS` or `KILLED`, and publishes one existing-schema claim or one immutable refusal marker. Other imports retain normal Python semantics, and global import state is unchanged. It does not reserve the classification attempt; the classification commit connector remains the sole owner of that marker.

Taey never supplies paths, card names, company names, titles, descriptions, job IDs, verdicts, or policy values. The classification preparation and commit invocations use frozen paths and digests bound by the trusted parent runtime. See [Private boundary](docs/PRIVATE_BOUNDARY.md), [Architecture](docs/ARCHITECTURE.md), the [classification runbook](docs/LINKEDIN_APPLICATION_CLASSIFICATION_RUNBOOK.md), and the versioned JSON schemas in [`schemas/`](schemas/).

The autonomous application preparer likewise accepts only a trusted private
root, one exact lifecycle file and digest, and public seat/correlation IDs.
The canonical mechanics and current adapter blocker are in the
[autonomous application runbook](docs/AUTONOMOUS_APPLICATION_RUNBOOK.md).

## Mechanical gates

```bash
python3 tools/check_public_boundary.py
python3 -m pip install .
python3 tools/validate_preparer.py
python3 tools/validate_contract.py
python3 tools/validate_classification.py
python3 tools/validate_classification_preparer.py
python3 tools/validate_application_boundary.py
```

These gates validate packaging and the deterministic data boundary with generated sanitized state. They do not replace exact-SHA review, deployment, or real Taey execution receipts. The production evidence qualified for the current baseline is recorded in the [canonical runbook](docs/LINKEDIN_APPLICATION_INTAKE_RUNBOOK.md#qualified-production-baseline).

## Status

Version `0.1.1` includes deterministic transaction preparation, capture-to-unclassified intake, and the additive classification commit connector. Public commit `253b882571673ae30d3beadda6f174439755a241`, through Presence commit `c42bd319b2fb8ef6b9774b6ef171293baf73e897`, produced two independently checked production intake receipts against the active application feed. Public classification commit `f3560aae4777bb8396ad8ae3cc98b2bec0dc23b1`, through Presence commit `7c2b1e79f346921c83a8829697b0ffcb0dfb9bc9`, then completed the bounded one-shot Taey classification acceptance recorded in the [classification runbook](docs/LINKEDIN_APPLICATION_CLASSIFICATION_RUNBOOK.md#qualified-production-baseline).

The provider-neutral autonomous layer has only mechanical fixture qualification;
it has no concrete Presence adapter and therefore makes no production-application
claim. Existing intake and classification production baselines are unchanged.
