# SSA-014 integration against SSA-001 (spec 0.2 demo)

This patch is based on the user-provided `main-after-ssa001.zip`. The old 002, 003 and 004 SQL files, Akamai extraction/embedding methods and SSA-001 repository remain unchanged.

## New/updated pieces

- `backend/domain/order_proposal.py`: strict order-only `propose_order` schema (`items`, `customer_name`, optional `callback_number`), which replaced the booking proposal in SSA-031. No authorization, price or total fields accepted. Where the rest of this guide says "booking", read "order".
- `backend/services/booking_chat.py`: workshop chat via one *proposal-only* tool. A 429 gets **one** short, bounded retry; no silent fake fallback.
- `backend/services/conversation.py`: `async handle_turn(interaction_id, text) -> str`, persists turns, deterministic read-back, later yes, calls gateway, then guarded execution. Success wording requires a committed booking row. Uses exact v0.2 escalation text.
- `backend/database/conversation_repository.py`: business-scoped adapter **using** SSA-001 `TranscriptRepository` methods. Does not replace it.
- `backend/database/migrations/005_ssa014.sql`: a small persisted pending-proposal table. SSA-001's `init_db.py` already loads `*.sql` in sorted order, so it applies automatically after `004_demo.sql`.
- `backend/services/receptionist_runtime.py`: reads public service names and prices from seeded DB; explicitly unavailable placeholders for missing neighbor services, never simulated authorization.
- `backend/api/chat.py`: `/chat` with opaque HMAC token derived from a strong team-configured `CHAT_SESSION_SECRET` and a server-owned UUID.
- `backend/main.py`, `backend/config.py`, `.env.example`: app-factory wiring, session secret and explicit missing-chat response.

## Running it in WSL/Docker

1. Work on your **SSA-014 feature branch**; inspect `git status` first. Do not discard teammate work to apply these changes. Use the supplied patch with `git apply --check` before applying, or merge individual files.
2. Add a **local secret** to `.env` (never commit it): `CHAT_SESSION_SECRET=<random-value-at-least-24-chars>`; generate via `python3 -c 'import secrets;print(secrets.token_urlsafe(32))'`.
3. To use the *real* chat model, set `OPENAI_API_KEY` to your authorized workshop key; use the existing `.env.example` names and respect the shared 60 req/min limit. No key means `POST /chat` returns HTTP 503, not fake inference.
4. Apply new migration and make sure Demo Salon is seeded:

   ```bash
   docker compose run --rm init python -m backend.scripts.init_db
   docker compose run --rm init python -m backend.scripts.seed
   docker compose up --build -d app
   ```

   Check `compose.yaml` service names first if they've changed; the original SSA-001 Compose names its app `app`.
5. `curl -X POST http://127.0.0.1:8000/chat -H 'Content-Type: application/json' -d '{"text":"One grilled pork banh mi for Sam"}'`. Keep the returned `interaction_id` and `conversation_token` for turn 2. This may request clarification depending on model behavior; the sandbox did **not** run live inference.
6. After reading back the proposed booking, send `{"interaction_id":"...","conversation_token":"...","text":"yes"}`. **Until SSA-010 and SSA-005 are plugged in, the action is proposed but NO booking is made.**

## Neighbors: exact integration boundary

**Update:** `create_app()` now defaults to the real gate and calendar through
`GateAdapter` and `BookingExecutor` in `backend/services/receptionist_runtime.py`.
Injection, shown below, is only needed in tests.

The conversation service accepts the gate and executor injected into the app factory:

```python
app = create_app(gateway=actual_gateway, executor=actual_booking_executor)
```

- `gateway.evaluate(action_id)` may be sync/async and returns an object or dict with `decision: "allow"|"escalate"|"clarify"` and `reasons` (optional list of dictionaries).
- `executor.execute(action_id)` may be sync/async but **must** execute only after a persisted `allow`/staff-approved decision and commit one `bookings` row in PostgreSQL. SSA-005 owns capacity checks, action state, and the unique booking row.
- `repo.get_booking_for_action(action_id)` must return the **committed** row; otherwise the receptionist does not say "booked".
- The gate owns retrieval/Jev and `human_reviews` creation on `escalate`, as defined in revised spec. The receptionist only speaks the fixed callback line.
- SSA-030 (browser) and SSA-023 (Twilio) create interactions with channel `web`/`phone` via `app.state.conversation_repository.create_interaction(settings.business_id, channel=..., external_reference=..., callback_number=...)`; then call `await app.state.conversation_service.handle_turn(interaction_id, completed_text)`. They must use an existing trusted call handle, not take an arbitrary browser interaction ID as authenticated.
- `handle_turn` is **async** in these files. A sync transport should await it in its own async handler rather than calling it like a regular function.
- `on_rate_limit` in `AkamaiBookingChat` can be wired to an optional transport "one moment" spoken acknowledgment. The HTTP `/chat` call itself retries once and returns a short fallback if the model remains unavailable.

## Known demo limitations

- New migration `005_ssa014.sql` is intentionally a narrow extension because confirmation across HTTP turns needs durable state. It does not add immutable payload hashes/epochs; those are deferred in v0.2.
- `interactions.callback_number` comes from customer speech when supplied, else from phone transport's caller ID, else remains null. It's never proof of caller authority.
- Review approval belongs to SSA-015, not this lane.
- No pre-delivery semantic filtering, revised-request parsing, voice transport, automatic retries of uncertain booking attempts, or production-grade account controls are claimed.
- No live Akamai request, real PostgreSQL transaction test or real booking was run by the assistant in this sandbox. Faked tests only validate local logic and API wiring. Run the integration suite against a disposable local PostgreSQL database:

  ```bash
  export TEST_DATABASE_URL=postgresql://demo:demo@localhost:5432/demo
  python3 -m pytest -q -m integration
  ```

- Do not point integration tests at the Akamai managed demo database. They create and destroy temporary schemas.

## Acceptance to complete SSA-014

- [ ] `POST /chat` obtains the real workshop `propose_booking` call and produces a spoken-friendly recap.
- [ ] The subsequent yes creates **one** `agent_actions` record, and the gateway runs once.
- [ ] `allow`: SSA-005 commits exactly one booking, and only then does the reply say booked.
- [ ] `escalate`: exact callback line, one pending review, **zero bookings**.
- [ ] `clarify`: invalid service/full slot asks to choose another, **zero bookings**.
- [ ] The browser microphone (SSA-030) and Twilio (SSA-023) call the same `handle_turn` and save segments in the correct channel.
- [ ] Manual live Akamai tool-call smoke test recorded, and the database integration tests passed locally.
