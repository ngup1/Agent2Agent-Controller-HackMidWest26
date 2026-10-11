# Provider compatibility notes

What was actually observed against live providers. Update the date when re-checked.

## Jev through the TypeSafe SDK (SSA-009)

Checked 2026-10-10 with the team key. References: the TypeSafe
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
