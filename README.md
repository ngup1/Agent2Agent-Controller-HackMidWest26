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

### SSA-015 dashboard checkpoint 3

The Jinja2 shell is at `/dashboard`. Set `DASHBOARD_PASSWORD` in the server
environment or a private `.env`, then restart Uvicorn. The browser's Basic Auth
dialog accepts any username (use `staff`) and the configured shared password.
An unset password disables the dashboard with HTTP 503; missing or wrong
credentials return HTTP 401. Dashboard routes share a router-level password check.
Use loopback for local development and HTTPS for any remote deployment.

The dashboard reads up to 100 reviews (pending first, oldest pending first),
the latest 20 calls, and the latest 20 bookings. Its queue refreshes every five
seconds; history and case pages refresh on page reload. Case links use the action
ID and show the transcript, proposal, rule checks, Jev evidence, precedents,
callback number and persisted booking result.

Case evidence follows the SSA-010 handoff: `policy_snapshot.checks`,
`policy_snapshot.precedents`, `policy_snapshot.providers`, and the `JEV_`
entries in `reasons`. Clarification cases show absent providers as not checked.
Chat mode is not recorded in that contract, so it is explicitly shown as such.
Provider modes are historical evidence, not proof of a successful live call.

To try the UI without a database, set `DASHBOARD_PREVIEW=true` and restart the
server. This is an explicit, synthetic, read-only preview with no database access.
It includes a group review and a clarification case; the second is linked under
recent calls. Turn preview off before database integration. Database errors
return HTTP 503 with a safe message and never fall back to synthetic records.

In database mode, pending cases offer Approve and book / Deny with an optional
staff note. Both POST through the protected dashboard router. Approval and the
SSA-005 booking connector share one transaction, holding the action and review
locks until commit. Denial records `denied` and `rejected` without execution.
An identical repeat is a no-op; a conflicting later decision returns HTTP 409.
The page reloads persisted evidence after submission and only shows a booking
confirmation when a booking row exists. Uncertain responses require a manual
reload, never an automatic decision retry.

The connector files were reused unchanged from `origin/main` at `961801c`
(SSA-005, PR #30), without merging unrelated lanes over local dashboard edits.
The gate is a separate integration dependency: a pending review must already
exist before the dashboard can decide it.

Validation: all 57 tests passed against the local Compose PostgreSQL/pgvector
container, including all ten database tests. The dashboard database tests
cover approval, denial, concurrent approval and rollback on execution error, plus
readback through the dashboard queries. They use isolated temporary schemas;
do not point them at a shared live database. Cloud integration, live providers,
and a browser click-through with persisted reviews remain unverified.
For PowerShell, explicitly configure the local test target before running tests:

```powershell
$env:TEST_DATABASE_URL = 'postgresql://demo:demo@127.0.0.1:5432/demo'
.\.venv\Scripts\python.exe -m pytest -q -rs
```

Starting the database alone does not set `TEST_DATABASE_URL` in your terminal.
Without it, the ten database tests intentionally skip.

For a local browser check, initialize and seed the local database, then run
`python -m backend.scripts.dashboard_cases create`. This creates two explicitly
synthetic pending cases named APPROVE and DENY. It bypasses the gate and calls no
providers. Re-running preserves the same cases and their decisions. Use
`--batch local-2` for a new pair when needed. The command rejects non-local
database hosts.

Run Uvicorn with `DASHBOARD_PREVIEW=false` and a configured password. Approve the
APPROVE case and deny the DENY case. Then run
`python -m backend.scripts.dashboard_cases status` to verify the first has
`review=approved, action=executed, bookings=1` and the second has
`review=denied, action=rejected, bookings=0`. Set `DATABASE_URL` to
`postgresql://demo:demo@127.0.0.1:5432/demo` for these commands on the host.
Preview remains read only and is never used as a fallback when the database fails.

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
`search_similar` searches raw transcripts. SSA-008 uses `PrecedentService` and
`search_precedents` to retrieve only staff-decided calls; see
[retrieval and replay](docs/ssa-008-precedents.md).

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
`thresholds`. Each Jev check records its own question ID, label, verdict, confidence,
answer probabilities, provider mode and model in its reason evidence.

`RETRIEVAL_MODE` and `JEV_MODE` select `fake`, `live`, or unset:

- `RETRIEVAL_MODE=fake`: real PostgreSQL/pgvector retrieval with deterministic
  fake embeddings. Staff-decided calls are eligible immediately. Vectors and
  evidence are labelled fake; this does not demonstrate semantic quality.
- `JEV_MODE=fake`: the explicit Jev stand-in returns no concern.
  `.env.example` selects both fake modes for local work.
- `JEV_MODE=live`: a real Jev decision through the TypeSafe SDK
  (`backend/providers/jev.py`). It needs `TYPESAFE_API_KEY`; without the key, or on
  a timeout, SDK error or unexpected answer, the gate escalates with
  `JEV_UNAVAILABLE`. See [provider notes](docs/provider-compatibility.md).
- `RETRIEVAL_MODE=live`: workshop embeddings and real pgvector retrieval through
  `PrecedentService.retrieve_for_action(action_id)`. Missing access, invalid
  vectors or retrieval errors escalate with `RETRIEVAL_UNAVAILABLE`. No fake
  provider is substituted.
- `EMBEDDING_MODE` selects embeddings for standalone service users only; the
  gate always uses `RETRIEVAL_MODE`. Fake/live vectors have different model tags.
  Use separate demo datasets when switching modes; cached vectors are not replaced.
- unset, a provider error, or a malformed result: `escalate` with risk `unknown`.

### SSA-009: three JEV checks

One TypeSafe request evaluates **instruction override**, **authority manipulation**,
and **intent mismatch**. The last check compares the actual proposed service, time
and party size with the customer's latest request, corrections and confirmation.
Jev receives the latest 12 speaker-labelled transcript turns through the proposal's
creation (up to 6,000 content characters), their timestamps, rule findings and up
to three reviewed precedents. Later messages cannot affect an earlier proposal's
assessment context. Truncation is explicit. Retrieval still embeds caller-only text.

Any concern, insufficient context, confidence below 0.80, missing answer or invalid
answer requires staff review. Valid answers are retained if another check fails.
Unavailable checks persist `verdict: UNAVAILABLE` and null confidence, so the
dashboard displays the failure explicitly.
The case page displays each check independently, including its answer probabilities;
confidence is not a probability that a booking is safe. Existing single-question
case records remain readable. No database migration is required.

Set `TYPESAFE_API_KEY` privately and select `JEV_MODE=live` in the environment used
to launch the app. For Compose with a deployment env file, pass
`docker compose --env-file .env.deploy ...`; Python's default `.env` loader does not
automatically read `.env.deploy`. Keep `RETRIEVAL_MODE` explicitly configured too.

Run the five synthetic live provider checks (real API usage; no bookings or database
access, only the TypeSafe key/model are read from the specified file):

```bash
.venv/bin/python -m backend.scripts.check_jev --live --env-file .env.deploy
# Optional: --case intent_mismatch
```

On 2026-10-11 all five expectations passed against `jev-1.13.0`. Two additional
live Jev calls through the app verified an allowed routine booking and an escalated
intent mismatch with no booking, with all three results persisted and rendered.
That app check used a disposable local PostgreSQL schema, fake chat and fake
embeddings; it does not verify deployed chat, voice or cloud services.
The automatic suite on the PR branch passed **254 tests**, including **48 PostgreSQL integration
tests**, with fake inference and no skips. See the provider notes for limitations.

Expected provider shapes: `retrieve(business_id, text, exclude_interaction_id)`
returns `[{interaction_id, summary, outcome, similarity}]`, and
`assess(action, transcript_excerpt, rule_checks, precedents)` returns
`{answers: {question_id: {verdict, confidence, probabilities}}, model}`. The three
required IDs are defined in `backend/domain/supervision.py`; unavailable answers
contain `verdict: UNAVAILABLE`, null confidence and a safe error. The transcript
argument is now a list of `{speaker, content, recorded_at}` turns, not caller-only
text. The gateway attaches the application-owned question labels to its reasons.

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
| Managed database and Linode deployment (SSA-021) | Deployed 2026-10-10 at `https://172-232-10-159.sslip.io`; HTTPS health, managed database and booking-after-restart verified; Twilio check pending. See the section below |
| Gate (SSA-010) | Rules, precedent retrieval and persistence integrated; fake/live modes labelled; one live retrieval rehearsal run on the deployed host (see SSA-021 section) |
| Precedent (SSA-008) | Real pgvector and application replay tested with fake inference; live recall rehearsed once on the deployed host; recordings and threshold calibration pending |
| Jev (SSA-009) | Live through the TypeSafe SDK when `JEV_MODE=live` and a key is set; three checks per request; five live scenarios and two local app flows verified on 2026-10-11 |
| Booking calendar (SSA-005) | Real persistence; see the section below |
| Receptionist (SSA-014) | Wired to the real gate and calendar; verified with a fake chat model only, live chat unverified |
| Dashboard (SSA-015) | See the SSA-015 section above |
| Voice | Unfinished; assigned to the other demo issues |

## Deploying on Akamai (SSA-021)

`deploy/` holds the cloud stack: `compose.yaml` (app and Caddy, no database
container), `Caddyfile`, `cloud-init.yaml` and `deploy_linode.sh`. The script is
adapted from the workshop's `scripts/deploy_linode.sh` at revision `e362ae2`.
Deliberate differences: Caddy serves HTTPS and WSS on 443 and port 8000 is not
published, SSH is limited to named addresses, and the Linode is created in the
database's region. The database is the team's existing `ssa-demo-pg`
(`DB_LABEL` overrides the label); the script never creates or deletes a database.

Needs `linode-cli` configured for the team account (`pip install linode-cli`,
then `linode-cli configure`) and an SSH key (`ssh-keygen -t ed25519`).

```bash
bash deploy/deploy_linode.sh plan      # read-only: account, region, price, what apply would do
bash deploy/deploy_linode.sh allow-me  # add this machine's /32 to the database allow list
bash deploy/deploy_linode.sh env       # write the managed DATABASE_URL into .env.deploy
bash deploy/deploy_linode.sh apply     # create firewall and Linode (billed), deploy, wait for HTTPS
bash deploy/deploy_linode.sh status    # resource IDs and health
```

- `plan` changes nothing. A person reviews the account, region and price before
  running `apply`, which creates a `g6-standard-1` Linode (`LINODE_TYPE` overrides).
- `.env.deploy` is the server's `.env`. `env` creates it from `.env` and replaces
  only `DATABASE_URL`; set the provider modes, API keys, `DASHBOARD_PASSWORD` and
  `CHAT_SESSION_SECRET` there before `apply`. It is ignored by git and copied over SSH.
- The host name defaults to `<linode-ip-with-dashes>.sslip.io`, so Caddy can get a
  certificate without a DNS record. Set `DOMAIN=…` to use a real name that already
  points at the Linode.
- The firewall accepts 22 from `SSH_ALLOW` (default: the current machine's `/32`)
  and 80/443 from anywhere. Running `apply` again from a new network updates the
  SSH rule and redeploys the code.
- The allow list gets `/32` entries only; the script refuses to work with a
  database open to `0.0.0.0/0`.
- Migrations and the seed run against the managed database on every `apply`.
  Both are repeatable; `002_governance.sql` creates the `vector` extension.
- The connection uses `sslmode=require` (encrypted, certificate not verified).
  Certificate verification and a separate runtime role are deferred.

After a successful `apply`, restart check: make a booking, run
`ssh root@<ip> "cd /opt/ssa && docker compose -f deploy/compose.yaml restart app"`,
and confirm the booking is still listed on the dashboard.

### Resource inventory

Fill in from the last lines of `apply` (or `status`). Delete these after the event;
a powered-off Linode is still billed.

| Resource | Label | ID | Region | Notes |
|---|---|---|---|---|
| Managed PostgreSQL | `ssa-demo-pg` | 537406 | `us-ord` | PostgreSQL 17, `g6-nanode-1`; created by the team outside this script |
| Cloud Firewall | `ssa-demo-fw` | 201851353 | n/a | 22 from one dev `/32`; 80/443 public |
| Linode | `ssa-demo-app` | 107879694 | `us-ord` | `g6-standard-1`, 172.232.10.159, `https://172-232-10-159.sslip.io` |

### SSA-021 acceptance record

| Check | Result |
|---|---|
| `/healthz` over HTTPS on the Linode reports the managed database reachable | Passed 2026-10-10: `{"status":"ok","database":"reachable"}` with a Let's Encrypt certificate; still healthy after `restart app` |
| A booking survives an app container restart on the deployed host | Passed 2026-10-10: a haircut booked through `POST /chat` with live `hackathon-chat` (gate providers `fake`) was still in the managed database after `restart app` |
| Twilio reaches `wss://…/voice/stream` | Not run yet; the voice route (SSA-023) is not on `main` |
| No secrets committed; database not open to all addresses | Passed 2026-10-10: allow list is one dev `/32` and the Linode `/32`; port 8000 closed publicly; `.env.deploy` ignored by git |

Checked locally only: the compose stack with a throwaway PostgreSQL container and
`DOMAIN=localhost` answered `/healthz` through Caddy over HTTPS, redirected port 80,
and stayed healthy after an app restart. The script's `plan`, `env`, `allow-me` and
guard paths were run against a stubbed `linode-cli` before the first real `apply`.

### Live rehearsal on the deployed host (2026-10-10)

Run through `POST /chat` at `https://172-232-10-159.sslip.io` with live
`hackathon-chat`, `RETRIEVAL_MODE=live` (live `hackathon-embedding`) and
`JEV_MODE=fake`. One run, three calls; not a calibration.

| Call | Outcome | Nearest precedent |
|---|---|---|
| Haircuts for three, Monday 2 pm | Escalated on `GROUP_SIZE`; denied on the dashboard | None (no decided calls yet) |
| Haircuts for three, Monday 3 pm | Escalated, risk high, `SIMILAR_TO_DENIED` added | The first call, denied, similarity 0.96 |
| Beard trim for one, Monday 11 am | Booked automatically | The first call, denied, similarity 0.78 (below 0.80, no flag) |

What it showed:

- Live recall works end to end: embed, store, pgvector search, gate reason and
  evidence on the case.
- `PRECEDENT_SIMILARITY_MIN=0.80` has little margin. An unrelated routine request
  scored 0.78 against a denied group booking. One sample only; calibrate on more
  calls before changing it.
- Chat replies took 3.7 to 12.5 seconds, against a 12-second model timeout in
  `backend/main.py`. One attempt at 12.5 seconds returned the "I couldn't
  understand that request" fallback and succeeded when repeated.
- A rejected or timed-out model call reaches the caller as that same fallback and
  writes nothing to the logs, so an invalid API key looks like a misunderstanding.
- The seed creates three days of slots from the day it runs; a request for the
  fourth day was refused as unavailable. Each `apply` re-seeds.

The managed database still holds this rehearsal's rows: two bookings (Test Caller,
Rehearsal Charlie), one denied review (Rehearsal Alpha, which acts as a precedent
for similar calls), one pending review (Rehearsal Bravo) and three embeddings.

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
A confirmed proposal now goes through the real gate (SSA-010) and, on `allow`,
the booking calendar (SSA-005); tests may still inject their own. Set
`RETRIEVAL_MODE` and `JEV_MODE`, or every bookable request goes to staff review.
A spoken yes such as "yeah" or "yes, that's correct" confirms; a reply containing
a change ("yes, but…") clears the proposal. The service the model sends is matched
to a seeded service by code or display name and stored as its code. No live
Akamai chat call has been verified yet.
See [SSA-014 integration guide](docs/ssa-014-integration.md) for wiring, tests,
and safe fallback behavior.
