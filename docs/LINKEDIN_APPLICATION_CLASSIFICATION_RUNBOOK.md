# LinkedIn application-classification runbook

This is the public mechanical contract for committing one private-parent classification decision to one qualified unclassified LinkedIn intake row. It does not evaluate policy and grants no scoring, scorer activation, feed mutation, ATS, UI, application, messaging, or outward authority.

## Parent-owned preparation

The private parent, outside this repository:

1. selects one row bound to one qualified intake receipt;
2. reads the row and its private inputs without mutation;
3. invokes the existing private classifier exactly once;
4. accepts only exact `PASS` or `KILLED`;
5. computes the complete prewrite row digest, stable non-verdict row digest, combined policy/input digest, and classifier-source digest;
6. writes one canonical owner-controlled `0400` claim matching `schemas/linkedin-classification-private-claim-v1.json`;
7. freezes its expected SHA-256 and invokes the connector once.

No raw job value, URL, policy rule, threshold, classifier source, or verdict enters Taey's request or the command line. The claim is private data. The public connector does not claim that it re-evaluated the private policy.

## Required private state

- The exact reviewed public checkout and explicit Python interpreter are pinned by the parent.
- The private root is a nonsymlink owner-controlled `0700` directory.
- The database is an existing nonsymlink owner-controlled `0600` file.
- The frozen claim, intake transaction, intake receipt, and four source files are owner-controlled `0400` files beneath the private root.
- `classification-attempts` and the receipt parent are owner-controlled nonsymlink `0700` directories beneath the private root.
- The intake receipt digest equals the claim's exact digest and validates as a successful unclassified intake with all three NULL postconditions.
- The scorer remains inactive. No service is started, stopped, or restarted for this transaction.

## One connector call

```bash
PYTHONPATH="$TAEY_APPLY_PUBLIC_ROOT/src" \
  "$TAEY_APPLY_PYTHON" -P -m taey_apply.classification_cli \
  --private-root "$TAEY_APPLY_PRIVATE_ROOT" \
  --database "$TAEY_APPLY_DATABASE" \
  --claim-file "$TAEY_APPLY_CLASSIFICATION_CLAIM" \
  --expected-claim-sha256 "$TAEY_APPLY_CLASSIFICATION_SHA256" \
  --receipt-file "$TAEY_APPLY_CLASSIFICATION_RECEIPT" \
  --requester "$TAEY_APPLY_REQUESTER" \
  --turn-id "$TAEY_APPLY_TURN_ID" \
  --correlation-id "$TAEY_APPLY_CORRELATION_ID" \
  --process-generation "$TAEY_APPLY_PROCESS_GENERATION"
```

That process invocation is the single attempt. Do not run it again after success, refusal, timeout, transport failure, `WRITE_INDETERMINATE`, `SIDE_EFFECT_UNCERTAIN`, or `RECEIPT_INDETERMINATE`.

## Terminal acceptance

Accept only one compact result with:

- schema `taey_apply_linkedin_classification_result_v1`;
- operation `classify_frozen_linkedin_intake`;
- state `classified`, `ok=true`, and `terminal=true`;
- exactly one record observed and written;
- transaction digest equal to the frozen claim digest;
- immutable receipt digest equal to exact receipt bytes.

Independently prove:

- the jobs count is unchanged;
- exactly one target row now has the private terminal verdict;
- target `score` and `applied_at` remain SQL `NULL`;
- `applications` and `apply_runs` counts are unchanged;
- the attempt marker and receipt are owner-controlled `0400` files;
- the compact result and receipt contain no raw job, URL, policy, classifier, account, credential, database path, or verdict value;
- the scorer remained inactive;
- no UI, feed, ATS, application, message, or outward action occurred.

The first mismatch is terminal. Preserve the claim, attempt marker, receipt or partial receipt, compact output, database measurement, and exact public commit for reconciliation. Do not delete a marker, reverse a verdict, create a substitute receipt, or retry automatically.

## Qualified production baseline

The bounded one-shot Taey acceptance completed on 2026-08-26 from clean public checkouts:

- `taey-apply` `f3560aae4777bb8396ad8ae3cc98b2bec0dc23b1`;
- `taey-presence` `7c2b1e79f346921c83a8829697b0ffcb0dfb9bc9`;
- immutable private terminal receipt SHA-256 `9732a6f1ef11679af0a67f26fa6524b8f02344946652673b0d4a7206ef710e38`.

The frozen Taey identity was invoked exactly once. The connector observed and
wrote exactly one row, changed only `verdict`, preserved the stable non-verdict
row digest, left `score` and `applied_at` SQL `NULL`, and recorded equal
before/after counts for jobs, applications, and apply runs inside the atomic
transition. The scorer remained inactive. The transaction, Presence claim,
connector attempt, terminal receipt, and captured completion response were all
owner-controlled mode-`0400` files. No display, UI, feed operation, ATS action,
application, message, or outward action occurred.

This qualification applies only to the pinned commits and receipt above. It is
not qualification of private policy evaluation, scoring, ATS operation, or the
application loop.
