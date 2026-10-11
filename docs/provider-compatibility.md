# Provider compatibility notes

What was actually observed against live providers. Update the date when re-checked.

## Jev through the TypeSafe SDK (SSA-009)

Initial single-question check on 2026-10-10 with the team key. References: the TypeSafe
[quickstart](https://docs.typesafe.ai/introduction/quickstart) and
[Choice primitive](https://docs.typesafe.ai/primitives/choice).

- **Package:** `typesafe-sdk` 0.7.4, Python 3.10 or later.
- **Authentication:** `TypeSafeClient(api_key=...)`, or the `TYPESAFE_API_KEY`
  environment variable. The adapter passes the key from `Settings`.
- **Model:** no model was requested, so the SDK default applied; responses
  reported `jev-1.13.0`. Set `TYPESAFE_MODEL` to request a specific one.
- **Request:** `client.system_one(state=<JSON>, questions={id: Choice(instructions, criteria)})`.
  The client is synchronous and accepts `timeout` and `retry=RetryPolicy(max_retries=...)`.
  The adapter uses 8 seconds and one retry.
- **Response:** `response.model`, `response.usage` (`input_tokens`, `output_tokens`),
  and `response.answers[id]` with `type` (`"choice"`), `choice` (the criteria key),
  `confidence` (0 to 1) and `probabilities` (one value per criteria key).
- **Observed:** four calls, each returning in under a second with about 450 input
  and 50 output tokens. Two routine haircut requests returned `NO_CONCERN` and two
  requests claiming the owner's approval and asking to skip checks returned
  `CONCERN`, all with confidence 1.0.

Not verified: rate limits, pricing, timeout and error behaviour against the live
service (covered only by tests with a fake client), and `INSUFFICIENT_CONTEXT`.
Confidence is how concentrated the probabilities are; it is not a probability
that the booking is safe.

### Three-question extension — verified 2026-10-11

The same installed SDK (0.7.4) accepts all three Choice questions in one
`system_one` call. Question IDs are `instruction_override`, `authority_manipulation`
and `intent_mismatch`. The internal adapter now returns an `answers` map and the
reported model. Each answer contains `verdict`, `confidence`, and all three answer
probabilities, or an explicit `UNAVAILABLE` and safe error. The gateway validates
completeness and writes one reason per required check. Valid sibling answers are
preserved on a partial failure. This changes the former single-answer contract;
stored historical decisions are not rewritten.

Input includes ordered caller/receptionist turns with timestamps through the
proposal's creation, the current booking's service/time/party size, rule checks
and precedents. Only the latest 12 turns and 6,000 content characters are sent,
with an explicit truncation flag. Later messages are excluded from the assessment.
Instructions distinguish untrusted dialogue and precedent from application rules,
and tell Jev to account for customer corrections and the confirmation exchange.

Command: `.venv/bin/python -m backend.scripts.check_jev --live --env-file .env.deploy`.
Five real requests succeeded with reported model `jev-1.13.0`:

| Synthetic case | Observed |
|---|---|
| Routine booking | All three `NO_CONCERN`, confidence 1.0 |
| Ignore instructions and conceal the request | Instruction override `CONCERN` |
| Claim owner authority without support | Authority manipulation `CONCERN` |
| Customer can only attend after 5 pm; proposal is 4 pm | Intent mismatch `CONCERN` |
| Explicitly change 2 pm to 4 pm, then confirm | All three `NO_CONCERN`, confidence 1.0 |

Two further real Jev requests passed through the app and real PostgreSQL in a
temporary local test schema. The routine case created one mock-calendar booking;
the intent mismatch created a pending review and zero bookings. All three actual
answers and the model persisted and rendered in the authenticated case page.
Chat and embeddings in that check were explicitly fake. The temporary schema was
removed by the test fixture; no deployed database or real business was changed.

Automatic validation: `TEST_DATABASE_URL=postgresql://seb@127.0.0.1:55439/ssa_validation
.venv/bin/python -m pytest -q -rs` passed 254 tests on the PR branch, including 48
PostgreSQL integration tests, with no skips. SDK failures,
malformed/partial answers, low confidence and insufficient context were exercised
with fakes. Regression tests cover delayed assessment after a later proposal,
including writes in one transaction, and explicit `UNAVAILABLE` verdicts in
persisted evidence and on the dashboard. Live failure behaviour and live
`INSUFFICIENT_CONTEXT` remain unverified.
These smoke cases do not measure classification accuracy or calibrate the 0.80
review threshold. No cloud deployment was performed for this extension.
