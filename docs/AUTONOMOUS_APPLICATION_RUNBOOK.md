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
There is no routine human review or approval field and no queue.

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

## Execute through one reviewed adapter

`application_runner.run_application` accepts an injected
`OneActionExecutor`. The executor receives one `OneActionRequest` and returns
one `OneActionOutcome`; the runner contains no transport configuration.

The concrete production adapter must be a reviewed public Presence contract
that delegates provider UI behavior to Hands. Until that contract is merged,
production binding is blocked. Do not substitute a private endpoint, shell
command, direct Hands import, or handwritten request shape.

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
```

The gate uses generated private fixtures and an injected executor. It proves
contract mechanics without a browser, account, applicant, employer, network
request, or external mutation. It is not production evidence.
