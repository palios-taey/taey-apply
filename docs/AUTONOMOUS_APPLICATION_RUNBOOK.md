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

Success requires an exact employer-confirmation route and anchor observed in at
least two distinct revisions. The terminal result always sets
`next_mutation_authorized` to `false`.

## Mechanical gate

```bash
python3 tools/validate_application_boundary.py
```

The gate uses generated private fixtures and an injected executor. It proves
contract mechanics without a browser, account, applicant, employer, network
request, or external mutation. It is not production evidence.
