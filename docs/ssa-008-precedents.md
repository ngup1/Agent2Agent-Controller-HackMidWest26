# SSA-008: staff-decided call retrieval

This implements the dedicated service option for
[SSA-008](https://github.com/ngup1/Agent2Agent-Controller-HackMidWest26/issues/9).
The demo scope and section 16 of `spec.md` remain authoritative. There are no new
tables, curation/publishing steps, background workers, or migration changes.

## Gateway integration (SSA-010)

```python
from backend.config import Settings
from backend.database.repository import TranscriptRepository
from backend.memory.precedents import PrecedentService, RetrievalUnavailable

settings = Settings.from_env()
precedents = PrecedentService(TranscriptRepository(), settings)
try:
    matches = precedents.retrieve_for_action(action_id)
except RetrievalUnavailable as error:
    # The gateway persists RETRIEVAL_UNAVAILABLE and escalates with unknown risk.
    # error.mode is live/fake; error.stage is transcript/embedding/search.
    ...
```

Success returns up to three dictionaries with `interaction_id`, `summary`,
`outcome` (`approved` or `denied`), and `similarity` (one minus cosine distance).
`[]` is a successful search with no eligible matches, not a provider failure.
The service never writes a gate decision, a review, or a booking.

The explicit-text interface is also available:
`retrieve_precedents(business_id, text, k=3, *, action_id=...)`. The action ID is
required to identify and exclude the current call. Business must equal server
configuration and text must equal the persisted caller-only proposal text.
Prefer `retrieve_for_action` so the gateway does not reconstruct either value.

The gateway alone applies `PRECEDENT_SIMILARITY_MIN` (default `0.80`): the nearest
match must be denied and meet the threshold to add `SIMILAR_TO_DENIED`. An
approved match never cancels a rule escalation. Pass all three matches to Jev
and the dashboard. Similarity is not a probability of maliciousness.

These synchronous methods should be called from a synchronous route/service or
offloaded to a thread when integrating into an async voice handler.

## Storage and proposal timing (SSA-014)

Persist caller segments before creating the proposal, then invoke retrieval.
Do not attach embeddings to every utterance. The service joins caller segments
through the proposal's creation time and attaches one request vector to the
last such caller segment. Repository inserts use `clock_timestamp()` so later
turns within the same database transaction are distinguishable from earlier
proposals; PostgreSQL's default `now()` is constant throughout a transaction.
Other code creating these rows must preserve that ordering/timestamp behavior.

Retries reuse a stored vector. Proposals made without an intervening caller turn
share the same request text and segment vector under the existing schema. This
does not add the deferred immutable-action/version machinery. Concurrent first
evaluations can issue redundant embedding requests, but the existing segment
primary key permits only one stored vector. The gateway should evaluate a new
proposal once in the normal demo flow.

Retrieval joins current `human_reviews` directly, so a dashboard decision is
immediately visible. It excludes pending/expired/unreviewed calls, the current
call, other businesses, and other embedding models. Each call contributes only
its latest decided proposal (review time, then action creation time and ID as
tie-breakers), so one call cannot fill all three slots. Exact cosine ranking is
sufficient for this small corpus. Summaries are the first 500 characters of the
matched proposal's caller text, not generated explanations.

## Provider modes and checks

`EMBEDDING_MODE=live` is the default and uses the existing
`AkamaiService.embed_transcript` adapter. Missing access, invalid dimensions,
nonfinite/zero vectors, provider failures, and database failures become explicit
unavailability. Expose `service.mode` in the dashboard provider badge; configured
live mode alone is not evidence that a live call succeeded.

`EMBEDDING_MODE=fake` explicitly selects deterministic token hashing for offline
tests. Its model tag is `fake-token-hash-v1-1024`, separate from the live model.
It verifies plumbing, not semantic retrieval quality. Switching modes requires
a fresh demo dataset: existing vectors are never silently overwritten or mixed.

The API shape, alias, and dimensions follow the pinned
[workshop inference reference](https://github.com/akamai-developers/akamai-workshop-hackmidwest/blob/e362ae2/docs/inference.md)
and [retrieval notebook](https://github.com/akamai-developers/akamai-workshop-hackmidwest/blob/e362ae2/notebooks/02_embeddings_and_retrieval.ipynb).

When the team key is available, explicitly run:

```bash
python -m backend.scripts.check_embeddings
python -m backend.scripts.check_embeddings --recordings fixtures/calls
```

These commands always use the live provider, even if the app is configured fake.
The first makes one synthetic embedding request and checks 1,024 finite nonzero
dimensions. The second also embeds the recorded calls and reports every routine
versus denied similarity; it exits unsuccessfully if any reaches the threshold.
It does not tune the threshold automatically or claim a held-out evaluation.
Also rehearse a paraphrase of a denied call and verify it is the nearest match
above the threshold on the dashboard.

## Recording replay (SSA-014, SSA-015, SSA-022)

See `fixtures/calls/README.md` for recording and export. Run the replay CLI with
`DATABASE_URL` pointing at the app's database and the same `DEMO_BUSINESS_ID`.
It creates an empty API interaction before sending turns, and derives a stable
UUID from the configured business and recording ID. A digest of the transcript
and saved outcome detects changed recordings. A completed repeat run reads the
persisted outcome and skips the call; it does not create duplicate proposals,
embeddings, reviews, or bookings. After a database reset the same calls rebuild.

The required HTTP adapter contract is:

- `POST /chat` accepts `{interaction_id, text}` for that existing interaction,
  persists the turn, and finishes its synchronous proposal/gate work before
  replying. Replay reads outcomes from PostgreSQL, not response prose.
- The dashboard decision route accepts POST at the explicitly supplied
  `--review-path` template. Placeholders are `{review_id}`, `{action_id}`, and
  `{decision}` (`approve`/`deny`). The request body includes `status`
  (`approved`/`denied`). The route owns the normal review/execution service call.
- If the dashboard uses HTTP Basic authentication, set
  `REPLAY_DASHBOARD_PASSWORD`; the adapter uses username `staff`. If that lane
  chooses a different password transport, adapt `HttpReplayTransport` to it.

Example once the dashboard supplies the matching route:

```bash
python -m backend.scripts.replay_calls --base-url http://127.0.0.1:8000 \
  --review-path '/reviews/{review_id}/{decision}'
```

The route template is an integration proposal, not a claim those routes already
exist on this branch. A recorded denied call must actually reach pending review
during replay; the script never fabricates a review to force the saved result.
Interrupted/changed replays fail explicitly and are not automatically resent.
Inspect them or use the demo reset flow before retrying. Recorded appointment
times still have to be valid against the seeded calendar when replayed.

## Verification and remaining evidence

Run the full suite against a disposable PostgreSQL/pgvector database:

```bash
TEST_DATABASE_URL=postgresql://demo:demo@localhost:5432/demo python -m pytest -q
```

Integration checks cover immediate denial eligibility, exclusion of undecided
and current calls, business/model filters, distinct top-three calls, proposal
boundaries, stored-vector reuse, and duplicate-free replay. Replay tests use an
explicitly fake HTTP application with a real database; they do not prove the
not-yet-integrated conversation/dashboard routes.

Verified locally on 2026-10-10 with disposable PostgreSQL 17 and pgvector 0.8.1:
`TEST_DATABASE_URL=postgresql://seb@127.0.0.1:55438/ssa008_test .venv/bin/python -m pytest -q`
reported **53 passed**, including regression coverage for excluding the entire
current call through both service entry points even when it has approved/denied
reviews and would otherwise be the strongest match. There was one existing FastAPI/Starlette TestClient
deprecation warning. `git diff --check` passed. The initial live smoke command
reported a missing key without making a live request.

The team subsequently supplied workshop access. On 2026-10-11 at 00:56 UTC
(2026-10-10 at 19:56 CDT), live checks against
`https://ws.akamai-workshops.com/v1` succeeded:

- `hackathon-embedding`: the implemented `LiveEmbeddingProvider` and existing
  Akamai adapter returned 1,024 finite, nonzero dimensions for a synthetic booking
  request, passing the service's vector validation (about 0.71 seconds).
- `hackathon-chat`: one small chat-completions request returned the requested
  word with finish reason `stop` (about 0.64 seconds). This verifies basic chat
  access, not the receptionist tool-calling workflow.
- Image generation was skipped because the demo does not require it.

The first concurrent smoke harness encountered a local Python import deadlock
before the embedding request. Running the embedding check sequentially succeeded.
For those checks, credentials were passed through standard input with terminal
echo disabled and used only in process memory. At the user's subsequent request,
the key was saved in the local gitignored `.env` with permissions `0600`; it is
not tracked or included in the PR.

Live embedding access is now verified. The team still has no recorded calls;
recorded-call calibration, a live retrieval rehearsal, and replay through the
actual conversation/dashboard routes remain pending. SSA-008 should not be
marked fully accepted until those results are recorded. Future local runs still
need the team key supplied through the environment or a gitignored `.env` file.
