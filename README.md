# Supervised Service Agent — Hack Midwest

[SSA-001](https://github.com/ngup1/Agent2Agent-Controller-HackMidWest26/issues/2)
establishes the FastAPI/PostgreSQL foundation for the demo in [spec.md](spec.md).
The shared application package is `backend/`; see [the agent guide](docs/agent-guide.md).

SSA-008 adds a dedicated [precedent retrieval service](docs/ssa-008-precedents.md)
with explicit live/fake embedding modes and recording export/replay commands.
Live embedding and basic chat requests were verified against the workshop endpoint
on 2026-10-10. Recorded-call calibration and conversation/dashboard replay
integration are still pending. Supply the team key locally; it is not in the repository.

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

| Component | Status in SSA-001 |
|---|---|
| PostgreSQL/pgvector and health endpoint | Real persistence; integration tests require a local database |
| Offline inference tests | Explicit fake clients |
| Akamai adapter | Reused; live calls skipped |
| Managed database | Unavailable locally; cloud verification pending SSA-021 |
| Jev, receptionist, voice, gate, calendar execution, dashboard | Unfinished; assigned to the other demo issues |

Validation of this rebuild: **20 tests passed** on Python 3.14 with real
PostgreSQL 17.6 and pgvector 0.8.1. Both CLI setup commands ran twice successfully;
a running Uvicorn server returned HTTP 200 with `database: reachable`.
`docker compose config -q` passed. Compose startup remains unverified because this
workspace cannot access the Docker daemon socket. Managed database and live
inference calls were not run.
