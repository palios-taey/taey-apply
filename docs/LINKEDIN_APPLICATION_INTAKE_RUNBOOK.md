# LinkedIn application-intake runbook

This is the only production sequence for turning one exact LinkedIn capture pair into one unclassified application-intake row. It grants no classification, scoring, ATS, application, or messaging authority.

## Required state

- Set `TAEY_APPLY_PUBLIC_ROOT` to the canonical absolute path of the exact reviewed and deployed public `taey-apply` checkout. Its checked-out commit must equal the approved commit, and `$TAEY_APPLY_PUBLIC_ROOT/src/taey_apply/prepare_cli.py` must be a regular file from that commit.
- Set `TAEY_APPLY_PYTHON` to the canonical absolute path of the exact executable Python interpreter configured for Presence.
- Configure `TAEY_APPLY_PRIVATE_ROOT` as an owner-controlled, nonsymlink `0700` directory.
- Place the four immutable source artifact/receipt files beneath that root at mode `0400`.
- Place one owner-controlled `0400` draft beneath that root. The draft contains exactly the seven fields in `schemas/linkedin-intake-private-input-v1.json`. It may contain formatting whitespace; private field values never appear on the command line.
- Choose a wholly new public-safe seat, event, and correlation identity. Use the same seat and correlation for preparation and the Taey call.
- Confirm the Presence proxy is healthy, has zero active turns, and is bound to the same database identity as the active application feed.

Do not manually create the final transaction, claim, or receipt. Do not reuse an identity whose transaction, seat parent, claim, or receipt exists.

The preparation command does not use an installed console script, an activated environment, ambient `PYTHONPATH`, `PATH`, or the current directory. Its exact `PYTHONPATH` assignment selects only the deployed public `src` tree, and Python `-P` prevents a working-directory package from shadowing it.

## 1. Prepare once

```bash
TAEY_APPLY_PREPARATION_RESULT="$(
  PYTHONPATH="$TAEY_APPLY_PUBLIC_ROOT/src" \
    "$TAEY_APPLY_PYTHON" -P -m taey_apply.prepare_cli \
    --private-root "$TAEY_APPLY_PRIVATE_ROOT" \
    --draft-file "$TAEY_APPLY_DRAFT_FILE" \
    --seat-id "$TAEY_APPLY_SEAT_ID" \
    --correlation-id "$TAEY_APPLY_CORRELATION_ID"
)"
```

After validating the safe root and public-safe identities, the preparer reserves fresh transaction, claim, and receipt seat parents at `0700`. It then validates strict JSON, exact fields, safe private-root references, and the existing public four-source pairing contract; writes the canonical final transaction once with `O_EXCL`; fsyncs it; freezes it at `0400`; and reads it back through the production connector contract.

If draft, pairing, or final readback validation fails after identity acceptance, the preparer writes a canonical `taey_apply_linkedin_intake_preparation_refusal_v1` terminal marker to the derived receipt path with `O_EXCL`, fsync, and mode `0400`. Presence sees the occupied receipt and refuses the transaction. Later preparation sees the occupied seat parents and refuses reuse. Never delete the marker, repair the identity, or try it again. An invalid private root or invalid public identity is rejected before an identity is accepted and creates no marker.

## 2. Verify before Taey

```bash
printf '%s\n' "$TAEY_APPLY_PREPARATION_RESULT" | python3 -c '
import json, sys
value = json.load(sys.stdin)
required = {
    "schema": "taey_apply_linkedin_intake_preparation_v1",
    "ok": True,
    "state": "prepared_unclaimed",
    "seat_id": sys.argv[1],
    "correlation_id": sys.argv[2],
    "transaction_mode": "0400",
    "canonical_no_trailing_newline": True,
    "claim_absent": True,
    "receipt_absent": True,
    "source_file_count": 4,
    "card_match_count": 1,
}
assert all(value.get(key) == expected for key, expected in required.items())
assert value.get("parent_modes") == {
    "claims": "0700", "receipts": "0700", "transactions": "0700"
}
assert len(value.get("transaction_sha256", "")) == 64
assert len(value.get("job_identity_sha256", "")) == 64
print(value["transaction_sha256"])
' "$TAEY_APPLY_SEAT_ID" "$TAEY_APPLY_CORRELATION_ID"
```

Any failed assertion is a full stop. Do not invoke Taey and do not run the preparer again for that identity.

## 3. Invoke Taey once

```bash
TAEY_APPLY_REQUEST_BODY="$(python3 -c '
import json, sys
print(json.dumps({
    "model": sys.argv[1],
    "stream": False,
    "messages": [{
        "role": "user",
        "content": "Execute the frozen LinkedIn application-intake transaction.",
    }],
}, separators=(",", ":")))
' "$TAEY_SERVED_MODEL_ID")"

curl --fail-with-body --silent --show-error \
  -H 'Content-Type: application/json' \
  -H "X-Taey-Seat-Id: $TAEY_APPLY_SEAT_ID" \
  -H "X-Taey-Event-Id: $TAEY_APPLY_EVENT_ID" \
  -H "X-Taey-Correlation-Id: $TAEY_APPLY_CORRELATION_ID" \
  -H 'X-Taey-Tool-Profile: linkedin-application-intake' \
  --data-binary "$TAEY_APPLY_REQUEST_BODY" \
  "$TAEY_PRESENCE_PROXY_URL/v1/chat/completions"
```

That `curl` command is the single Taey attempt. A timeout, transport error, refusal, claim, connector failure, or ambiguous response spends the identity. Never repeat it.

## Terminal acceptance

Accept only one compact terminal receipt whose transaction SHA matches Step 2 and whose source digests match the prepared capture. Independently confirm:

- the exact capture row exists once in the database used by the active feed;
- a new row remains SQL `NULL` for `verdict`, `score`, and `applied_at`, or an already-present row matches the capture exactly;
- `applications` and `apply_runs` counts did not change;
- claim and receipt are owner-controlled `0400` regular files;
- the Presence proxy returned to zero active turns.

Do not proceed to filtering, scoring, ATS work, application submission, or messaging under this transaction.
