# Autonomous application boundary

This runbook is the canonical public contract for the provider-neutral
application layer. It does not qualify a live application run and does not name
or guess a concrete Presence transport.

## Fixed lifecycle

One private canonical lifecycle binds exactly six completed stages to the same
application identity:

1. discovery;
2. qualification;
3. deep research;
4. materials;
5. truthful applicant data;
6. submission authority within current policy.

Each binding points to one owner-controlled `0400` canonical JSON receipt and
its SHA-256. The lifecycle also binds one opaque `0400` application context,
one bounded one-action call budget, and derived envelope/result/refusal refs.
There is no human-review state, approval field, or review queue.

## Materialize completed evidence once

Before envelope preparation, a trusted parent freezes one manifest matching
`schemas/application-materialization-manifest-v1.json`. The manifest carries
canonical applicant facts, work evidence, and policy directly; each completed
stage artifact is an owner-controlled `0400` file beneath the explicit `0700`
private root. The manifest contains no output paths: public seat and correlation
identities derive the six receipt refs, opaque context ref, and lifecycle ref.

```bash
PYTHONPATH="$TAEY_APPLY_PUBLIC_ROOT/src" \
  "$TAEY_APPLY_PYTHON" -P -m taey_apply.application_materialize_cli \
  --private-root "$TAEY_APPLY_PRIVATE_ROOT" \
  --manifest-file "$TAEY_APPLY_MATERIALIZATION_MANIFEST" \
  --manifest-sha256 "$TAEY_APPLY_MATERIALIZATION_MANIFEST_SHA256" \
  --seat-id "$TAEY_APPLY_SEAT_ID" \
  --correlation-id "$TAEY_APPLY_CORRELATION_ID"
```

Success returns only counts, IDs, and digests. The private lifecycle is written
at `application-materializations/SEAT/CORRELATION.lifecycle.json`; its SHA-256
is the returned `lifecycle_sha256` and is ready for the existing prepare command
below. The opaque context carries the exact fact, work-evidence, material, and
policy bindings that a later one-action sequencer needs when comparing newly
observed live options. The materializer does not choose an option or touch UI.
Missing required facts stop as `missing_truthful_applicant_data`.

## Prepare once

Production uses the exact reviewed checkout and explicit interpreter:

```bash
PYTHONPATH="$TAEY_APPLY_PUBLIC_ROOT/src" \
  "$TAEY_APPLY_PYTHON" -P -m taey_apply.application_prepare_cli \
  --private-root "$TAEY_APPLY_PRIVATE_ROOT" \
  --lifecycle-file "$TAEY_APPLY_LIFECYCLE_FILE" \
  --lifecycle-sha256 "$TAEY_APPLY_LIFECYCLE_SHA256" \
  --seat-id "$TAEY_APPLY_SEAT_ID" \
  --correlation-id "$TAEY_APPLY_CORRELATION_ID"
```

A successful preparation returns `prepared_unclaimed`, six evidence gates, the
envelope digest, and only public-safe IDs/counts. Failure after identity
reservation writes one immutable refusal. A prepared or refused identity is
never reused.

## Compile one current action

The public `GreenhouseActionCompiler` is the deterministic boundary between one
Taey surface decision and the existing Hands action envelope. Construct it with
the explicit private root, seat, display, and reviewed Hands commit. Its first
`compile` call accepts no capsule or decision and freezes only `observe_form`.
Each later call accepts:

- the current `OneActionRequest` and exact prior receipt hash;
- one validated bounded `ats_greenhouse_next_action_surface_v1` capsule;
- one decision matching `schemas/greenhouse-action-decision-v1.json`;
- fresh explicit event and correlation IDs.

The decision carries no applicant value or artifact path. Taey cites the exact
current ref/revision and a fact key for text, combo, or choice actions. On a
fresh options capsule, Taey cites only `select_option`, the revision, and a fact
key; ref and option name remain null. For an exact `Country` / `combo box`
origin, every option must carry one nonempty unique public `semantic_token`.
The private compiler requires one exact immutable-fact/token match and freezes
that control's untouched rendered name for postcondition validation. Every
other origin must carry no token and continues to require exact immutable-fact
/ rendered-name equality. Prefix, fuzzy, and normalized matching are absent.
The compiler validates cited work-evidence keys, copies private values and paths
from the immutable context, and writes exactly:

```text
PRIVATE_ROOT/actions/SEAT/CORRELATION.json
PRIVATE_ROOT/transactions/SEAT/CORRELATION.json
```

Both are canonical owner-controlled `0400` files. The latter is the exact
manifest read by Presence `greenhouse-ats-ui`. One action ID is derived from the
envelope, sequence, correlation, decision digest, and capsule digest. The
transaction ID stays stable across the application and every later action binds
the prior Hands event hash. An occupied identity, missing fact, stale revision,
unmapped question, wrong fresh option, native chooser order mismatch, or missing
artifact is terminal. There is no human review, approval, queue, retry, dynamic
generic mapping, endpoint, network request, shell, or direct Hands import.

Submit remains mechanically closed unless the bounded Hands/Presence capsule
contains exactly `required_controls_complete: true`. Hands
`7835effee404d6468e3b41a40b3708f2e19bfcf6` computes, exposes, and rechecks that
proof, and the reviewed Presence contract validates and relays it. The compiler
stops before writing a Submit action when the proof is absent or false.

## Execute through the reviewed adapter

`application_runner.run_application` accepts an injected
`OneActionExecutor`. The executor receives one `OneActionRequest` and returns
one `OneActionOutcome`; the runner contains no transport configuration.

An exact reviewed Presence checkout must expose the
`POST /v1/greenhouse-ats/one-action` route. It accepts an exact display-only
body under the `greenhouse-ats-ui` tool profile, performs the opaque
observe/operate pair, returns the raw terminal tool object, and stops after the
first refusal. `GreenhousePresenceOneActionExecutor` binds that route to the
runner. The first call compiles the read-only observation. Each later call
obtains one native schema-constrained Taey decision from the current bounded
capsule, publishes one frozen action/manifest, and invokes Presence once using
exact identity headers and a display-only body. Only fact/evidence key names
reach Taey; applicant values and artifact paths remain private and are copied
by the compiler after the decision. Generic model prose is never a decision,
`surface_capsule`, receipt, or `employer_confirmation`.

Production invokes the installed executable with explicit values; it loads no
environment file and has no endpoint default:

```bash
taey-run-application \
  --private-root "$TAEY_APPLY_PRIVATE_ROOT" \
  --envelope-file "$TAEY_APPLY_ENVELOPE_FILE" \
  --envelope-sha256 "$TAEY_APPLY_ENVELOPE_SHA256" \
  --seat-id "$TAEY_APPLY_SEAT_ID" \
  --display "$TAEY_APPLY_DISPLAY" \
  --hands-commit "$TAEY_APPLY_HANDS_COMMIT" \
  --event-id "$TAEY_APPLY_EVENT_ID" \
  --correlation-id "$TAEY_APPLY_CORRELATION_ID" \
  --taey-decision-endpoint "$TAEY_APPLY_TAEY_DECISION_ENDPOINT" \
  --taey-model "$TAEY_APPLY_TAEY_MODEL" \
  --presence-endpoint "$TAEY_APPLY_PRESENCE_ENDPOINT"
```

Every identity is single-use. Any missing truthful value, unmapped surface,
stale ref/revision, malformed schema decision, Presence lineage mismatch, or
side-effect uncertainty terminates the executor. There is no review or
approval queue and no retry path.

For each accepted call the runner requires:

- the same application identity;
- a fresh action ID and receipt digest;
- exact previous-receipt chaining;
- zero mutations for a proved observation or exactly one mutation for a proved
  action;
- an exact postcondition digest before another mutation is authorized.

The only terminal stop codes are:

- `exact_postcondition_failure`;
- `unmapped_ui_or_question`;
- `missing_truthful_applicant_data`;
- `policy_or_authority_boundary`;
- `side_effect_uncertainty`.

Success requires an exact employer-confirmation route and anchor, one stable
surface revision observed in at least two consecutive matched samples, the
digest of those samples, and an exact binding to the terminal executor receipt.
The terminal result always sets `next_mutation_authorized` to `false`.

## Mechanical gate

```bash
python3 tools/validate_application_boundary.py
python3 tools/validate_application_materializer.py
python3 tools/validate_application_action_compiler.py
python3 tools/validate_application_executor.py
```

The gate uses generated private fixtures and an injected executor. It proves
contract mechanics without a browser, account, applicant, employer, network
request, or external mutation. It is not production evidence.
