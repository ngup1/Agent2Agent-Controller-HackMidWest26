# Supervised Service Agent — Hack Midwest

[SSA-001](https://github.com/ngup1/Agent2Agent-Controller-HackMidWest26/issues/2)
establishes the FastAPI/PostgreSQL foundation for the demo in [spec.md](spec.md).
The shared application package is `backend/`; see [the agent guide](docs/agent-guide.md).

## Local setup

Requires Docker with Compose and permission to access its daemon:

```bash
cp .env.example .env
docker compose up --build
curl --fail http://localhost:8000/healthz
# {"status":"ok","database":"reachable"}
```

Compose starts `pgvector/pgvector:pg17`, waits for PostgreSQL, runs migrations and
seed, then starts the app. `/healthz` makes a real database query; connection
failure returns HTTP 503 with `database: unavailable`. It does not check inference.
Records persist in the `demo-db` volume across app/database restarts. Synthetic
local credentials and published ports are bound to loopback. Set `DATABASE_PORT`
and `APP_PORT` to override host ports.

Rerun migrations and seed:

```bash
docker compose run --rm init python -m backend.scripts.init_db
docker compose run --rm init python -m backend.scripts.seed
```

`002_governance.sql` and `003_agent_protection.sql` are unchanged from
`origin/databases` at `b2729e3`. They are applied in order, then `004_demo.sql`.
These tables do not depend on the workshop's starter `001`. The capability/audit
tables inherited from 002/003 are unused by this demo.

The repeatable seed inserts one server-configured **Demo Salon**, section 4.2
booking rules, `haircut` and `beard_trim` services, and half-hour slots from
09:00–17:00 Chicago time for today and the next two days. Only future slots are
generated; timestamps are stored in UTC and capacity is 3 per service slot.
Reruns preserve existing rules, service edits and slot closures/capacity, and fill
an empty booking policy when upgrading an older database. No precedents, calls or
bookings are fabricated by seeding.

An injectable clock supports reproducible schedules (1–14 days):

```bash
docker compose run --rm init python -m backend.scripts.seed --now 2026-10-10T08:00:00-05:00 --days 3
```

## Host Python and tests

Python 3.11+ (the container uses 3.12):

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
docker compose up -d db
export DATABASE_URL=postgresql://demo:demo@localhost:5432/demo
python -m backend.scripts.init_db
python -m backend.scripts.seed
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Environment variables override `.env`. Host Python uses `localhost`; Compose uses
the `db` service. If running the Compose app, stop it before using host port 8000.

Offline tests use explicitly fake provider clients and mocked connections:

```bash
python -m pytest -q -m 'not integration'
```

Real integration tests require a **disposable PostgreSQL/pgvector database**:

```bash
export TEST_DATABASE_URL=postgresql://demo:demo@localhost:5432/demo
python -m pytest -q -m integration
```

Tests create and drop uniquely named schemas and require schema/extension creation
permission. They run migrations and seed twice, check persisted transcripts and
embeddings, proposals and decisions, shared transaction rollback, schema vocabulary,
one booking per action, and a real `/healthz` request. With `TEST_DATABASE_URL`
unset, integration tests explicitly skip. No test makes paid inference calls.

## Shared lane contracts

`Settings.from_env().business_id` and `app.state.settings.business_id` resolve
`DEMO_BUSINESS_ID` from server configuration. Adapters must use this identity,
never a caller-supplied business ID. The seed uses the same setting.

`TranscriptRepository` retains the reusable branch's API and adds:

- `create_interaction(business_id, channel='api', external_reference=None, callback_number=None)`
- `save_segment(business_id, interaction_id, transcript, embedding=None, speaker='caller')`
- `attach_embedding(business_id, segment_id, embedding, embedding_model=None)`
- `create_action(business_id, interaction_id, payload, action_type='book_appointment')`
- `record_decision(business_id, action_id, decision, risk_level, reasons, policy_snapshot)`

Save text before calling the embedding provider so a provider failure cannot erase
the transcript. Attachment returns `True` for a new embedding, `False` if already
attached; it preserves the first vector/model. Vectors have 1,024 dimensions.
`search_similar` still searches raw transcripts; reviewed precedent retrieval is SSA-008.

An action starts as `proposed` with a generated `idempotency_key`. Recording a
decision stores gate evidence without changing state or executing anything.
Payloads follow the booking proposal in the spec. The receptionist and gate lanes
own proposal validation and workflow orchestration.

By default, repository methods each use a connection transaction. To compose
several calls inside a service-owned transaction, pass
`connection_factory=lambda: contextlib.nullcontext(conn)` using that transaction's
connection. Helpers do not explicitly commit it.

Use the existing schema vocabulary: channels `phone`, `web`, `api`; states
`proposed`, `allowed`, `pending_review`, `rejected`, `executed`, `failed`;
decisions `allow`, `escalate`, `clarify`; review statuses `pending`, `approved`, `denied`.

Calendar columns are `services.code/name/duration_minutes/price_usd/active`,
`slots.service_id/start_time/end_time/capacity/closed`, and
`bookings.action_id/slot_id/party_size/customer_name/callback_number`, plus IDs and
business IDs. `bookings.action_id` is unique. Capacity enforcement and execution
belong to SSA-005; the table alone does not enforce workflow authorization.

## Gate (SSA-010)

`backend.services.gateway.evaluate(action_id)` decides one `proposed` booking and
returns a `GateDecision` (`decision`, `risk_level`, `reasons`, `policy_snapshot`,
plus `to_dict()`) in the spec's Demo scope shape. It books nothing. In one
transaction it writes one `security_decisions` row and moves the action:

| Decision | Action state | Also written |
|---|---|---|
| `allow` | `allowed` | — |
| `escalate` | `pending_review` | a `pending` `human_reviews` row; `interactions.status = 'escalated'` |
| `clarify` | `rejected` | — |

Calling it again for a decided action returns the stored decision and writes nothing.

Order: rules (`backend/policy/booking_rules.py`), then precedent, then Jev. A
`clarify` stops after the rules and calls no provider. Notice under 30 minutes is
`clarify` with `SLOT_UNAVAILABLE`; 30–119 minutes is `escalate` with `SHORT_NOTICE`.
A party below one reuses `PARTY_TOO_LARGE`, because the contract has no other code.
A start time without an offset is read as business-local time.

Besides `rules` and `checks`, `policy_snapshot` holds `precedents` (up to three,
nearest first, approved or denied), `providers` (the mode each provider ran in) and
`thresholds`. Jev's verdict, confidence, question and mode are in its reason's evidence.

`RETRIEVAL_MODE` and `JEV_MODE` select `fake`, `live`, or unset:

- `fake`: gate stand-ins. Retrieval returns no precedents and Jev returns no
  concern. Both are recorded as `fake`. `.env.example` uses this for local work.
- `JEV_MODE=live`: a real Jev decision through the TypeSafe SDK
  (`backend/providers/jev.py`). It needs `TYPESAFE_API_KEY`; without the key, or on
  a timeout, SDK error or unexpected answer, the gate escalates with
  `JEV_UNAVAILABLE`. See [provider notes](docs/provider-compatibility.md).
- `RETRIEVAL_MODE=live`: **not integrated yet.** `live_retrieve` in `gateway.py`
  raises until SSA-008 replaces its body, so every bookable request escalates.
- unset, a provider error, or a malformed result: `escalate` with risk `unknown`.

Expected provider shapes: `retrieve(business_id, text, exclude_interaction_id)`
returns `[{interaction_id, summary, outcome, similarity}]`, and
`assess(action, transcript_excerpt, rule_checks, precedents)` returns
`{verdict, confidence, question}`.

## Managed database and integration status

The [Akamai memory lab](https://github.com/akamai-developers/akamai-workshop-hackmidwest/blob/e362ae2/docs/02_memory.md)
is the reference at revision `e362ae2`. SSA-021 handles paid provisioning. The same
code and scripts target a managed database by changing `DATABASE_URL` only:

```ini
DATABASE_URL=postgresql://akmadmin:ENCODED_PASSWORD@HOST:PORT/defaultdb?sslmode=require
```

Use the actual service host/port and percent-encode the password. Keep credentials
in `.env` or server environment variables. Then run migrations, seed and `/healthz`.

The inherited Akamai adapter is in `backend/providers/akamai.py`; imports from
`backend/services/akamai.py` remain compatible. Workshop variables
`OPENAI_BASE_URL`, `OPENAI_API_KEY`, `CHAT_MODEL`, `EMBEDDING_MODEL`, `DATABASE_URL`
are retained. A live adapter requires a key and propagates failures; it does not
substitute a fake client.

| Component | Status |
|---|---|
| PostgreSQL/pgvector and health endpoint (SSA-001) | Real persistence; integration tests require a local database |
| Mock calendar & execution connector (SSA-005) | Implemented (`backend/connectors/booking.py`); verified via 4 integration tests |
| Offline inference tests | Explicit fake clients |
| Akamai adapter | Reused; live calls skipped |
| Managed database | Unavailable locally; cloud verification pending SSA-021 |
| Gate (SSA-010) | Rules and persistence real; fake provider modes are labelled; live retrieval unfinished |
| Jev (SSA-009) | Live through the TypeSafe SDK when `JEV_MODE=live` and a key is set; four live calls verified locally; one question only |
| Booking calendar (SSA-005) | Real persistence; see the section below |
| Receptionist, voice, dashboard | Unfinished; assigned to the other demo issues |

## Testing SSA-005: Booking Calendar & Execution Connector

SSA-005 provides in-process booking calendar queries and atomic execution in `backend/connectors/booking.py`:
- `list_slots(service, day)`: Queries open 30-minute slots and computes available remaining capacity (`capacity - booked`).
- `execute(action_id)` (aliased as `book(action_id)`): Idempotently validates action state (`allowed` or `pending_review` with `approved`), row-locks slot capacity (`FOR UPDATE`), and writes the booking.

### How to test:

1. Ensure the PostgreSQL container is running:
   ```bash
   docker compose up -d db
   # Note: If port 5432 is already occupied on your host, use DATABASE_PORT=5433 docker compose up -d db
   ```

2. Point to the test database and run integration tests:
   ```bash
   export TEST_DATABASE_URL="postgresql://demo:demo@localhost:5432/demo" # or port 5433
   pytest tests/integration/test_booking.py -v
   ```

3. What the tests verify:
   - **Idempotency:** Executing the same action twice yields exactly one booking row.
   - **Capacity Guard:** Booking a full slot fails cleanly with state `failed` and reason `SLOT_FULL`.
   - **State Gate:** Actions that are `proposed`, `rejected`, or awaiting review cannot be executed.
   - **Durability:** Bookings persist in PostgreSQL across connections.


Validation of this rebuild: **20 tests passed** on Python 3.14 with real
PostgreSQL 17.6 and pgvector 0.8.1. Both CLI setup commands ran twice successfully;
a running Uvicorn server returned HTTP 200 with `database: reachable`.
`docker compose config -q` passed. Compose startup remains unverified because this
workspace cannot access the Docker daemon socket. Managed database and live
inference calls were not run.

## SSA-014 receptionist (in-progress)

`POST /chat` and `handle_turn(interaction_id, text)` are now implemented. The route
requires a server-owned `CHAT_SESSION_SECRET` (24+ characters) and live
`OPENAI_API_KEY`; neither is committed. Run `python3 -m backend.scripts.init_db`
to apply the new `005_ssa014.sql` pending-proposal table after SSA-001 migrations.
Confirmation creates an action atomically, but **no booking can complete** until
SSA-010's gateway and SSA-005's booking executor are connected to the app factory.
See [SSA-014 integration guide](docs/ssa-014-integration.md) for wiring, tests,
and safe fallback behavior.
