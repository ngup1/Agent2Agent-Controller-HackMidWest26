# Supervised Service Agent — Hack Midwest

## Deployed app

The demo runs on an Akamai Linode with Akamai Managed PostgreSQL at
**<https://172-232-10-159.sslip.io>**. The site has no home page; use these paths:

| Path | What it is |
|---|---|
| [`/healthz`](https://172-232-10-159.sslip.io/healthz) | Health check, including the managed database |
| [`/dashboard`](https://172-232-10-159.sslip.io/dashboard) | Incoming request board, case pages and shop settings; asks for the dashboard password |
| `POST /chat` | Text entry to the receptionist |
| `POST /voice/incoming`, `wss://…/voice/stream` | Twilio phone path (SSA-023) |

Deployment details, resource IDs and verification results are under
[Deploying on Akamai (SSA-021)](#deploying-on-akamai-ssa-021).

The host was moved to the banh mi order build on 2026-10-11 (`apply`, then
`seed --reset`, run by the owner). It does not yet have the later prompt fix that
stops the receptionist asking for a phone number; see
## Demo execution guide

The system connects three interactive channels to a unified Supervisor Gate, Policy Engine, and Staff Dashboard:

```mermaid
flowchart LR
    A["🤖 1. Agent-to-Agent\n(Autonomous /chat)"] --> G["🛡️ Supervisor Gate\n(Policy & Limit Checks)"]
    B["📞 2. Inbound Voice\n(+1 229-490-2249)"] --> G
    G --> D["🖥️ 3. Staff Dashboard\n(/dashboard)"]
    D --> O["📱 4. Outbound Voice\n(Twilio Callback)"]
```

### 1. Agent-to-Agent autonomous session

Simulates customer AI assistant (Agent A) negotiating an order directly with the restaurant receptionist AI (Agent B) over `POST /chat`, with spoken voice audio synthesis (`--voice`):

```bash
# Terminal 1: Keep server running locally (or use the deployed host)
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload

# Terminal 2: Run Agent-to-Agent autonomous booking session
python -m backend.scripts.agent2agent --scenario pork --phone +13145850116 --voice
```

To run against the live cloud deployment instead of localhost, pass `--url`:
```bash
python -m backend.scripts.agent2agent --url https://172-232-10-159.sslip.io --scenario pork --phone +13145850116 --voice
```

**Supported Scenarios:**
- `--scenario pork` *(Default)*: 1 Grilled pork banh mi ($8.50).
- `--scenario large`: 2 Party trays ($120.00 > $100 limit → triggers `ORDER_OVER_LIMIT` escalation).
- `--scenario allergy`: Tofu banh mi with peanut allergy mention (→ triggers `KEYWORD_RULE` allergen escalation).
- `--scenario manipulation`: Adversarial prompt injection attempt (→ blocked by Gate).

**Outcome:**
The request escalates to human review, and appears instantly on the staff dashboard under **Needs action**.

---

### 2. Inbound voice calls (Twilio Voice)

Customers can dial in from any mobile phone directly:

- **Live Twilio Phone Number:** `+1 (229) 490-2249`
- **Connected Host:** `https://172-232-10-159.sslip.io/voice/incoming`

**How it works:**
1. Call `+1 (229) 490-2249` from your cell phone.
2. The AI receptionist answers live: *"Hello, thanks for calling Banh Mi Shop. What can I get for you?"*
3. Speak your order: *"I'd like to order one grilled pork banh mi, the name is Seyun."*
4. Receptionist recaps: *"1 Grilled pork banh mi, $8.50 total, under Seyun. Is that correct?"*
5. Confirm: *"Yes, that's correct."*
6. Receptionist responds: *"I've passed this to our staff. Someone will call you back shortly to confirm. Your order is not placed yet."*
7. The call ends, and the order card appears live on the Staff Dashboard with the caller's phone number.

*For local inbound testing through ngrok:*
```bash
ngrok http 8000
# In .env set PUBLIC_HOST=<ngrok-subdomain>.ngrok-free.app
# Update Twilio phone number voice_url to https://<ngrok-subdomain>.ngrok-free.app/voice/incoming
```

---

### 3. Staff review & outbound voice callback

1. Open the Staff Dashboard: `http://localhost:8000/dashboard` (or `https://172-232-10-159.sslip.io/dashboard`). Username: `staff`, Password: configured `DASHBOARD_PASSWORD`.
2. Locate the escalated card under the **Needs action** column.
3. Click the card to review transcript evidence, policies, and supervisor reasoning.
4. Click **Approve**.
5. The order connector executes the write to PostgreSQL, and Twilio Voice automatically dials the customer's phone number with a spoken confirmation:
   > *"Hello Seyun! This is the automated assistant from Demo Banh Mi. Our staff has reviewed and approved your order for grilled pork banh mi. Your order is now confirmed and being prepared. Thank you for ordering with us!"*

To test the outbound call directly from the command line:
```bash
python -m backend.services.outbound +13145850116 --name Seyun --service "grilled pork banh mi"
```

## Banh mi orders (SSA-031)

The demo business is now a banh mi shop taking pickup orders. The gate's shape
(rules, then precedent, then Jev), staff review and both voice inputs are
unchanged; the proposal, rules, reason codes and executed record are new. The
contract is in the Demo scope section of [spec.md](spec.md).

- **Order proposal:** `propose_order` with `items` (menu codes, repeated for more
  than one), `customer_name`, optional `callback_number`. A price or total from
  the model is rejected.
- **Preloaded menu (synthetic):** grilled pork $8.50, lemongrass chicken $8.50,
  tofu $8.00, cold cut special $9.00, Vietnamese iced coffee $4.50, party tray $60.00.
- **Preloaded rules:** a total above $100.00 goes to staff; a caller who says an
  allergy word (allergy, allergic, peanut, gluten, shellfish and others in
  `backend/scripts/seed.py`) goes to staff under "Allergy mention".
- **Where they live:** the menu is the `services` table and the rules are the
  `business_policies.booking_rules` JSON (inherited column name). The gate reads
  both on every decision, so an edit takes effect on the next order with no
  restart. Staff edit them at `/dashboard/settings` (see the dashboard section).
- **Earlier salon calls** stay in the database and on the dashboard. They are not
  used as precedent, because retrieval only considers `place_order` proposals.

What is verified, and how:

| Check | Result |
|---|---|
| Automated tests | 247 pass without a database; 300 pass with `TEST_DATABASE_URL` on local PostgreSQL 17 with pgvector (isolated schemas). Inference is fake in every test |
| Local rehearsal through `POST /chat`, 2026-10-11 | Live `hackathon-chat`, live `hackathon-embedding` retrieval, `JEV_MODE=fake`, local PostgreSQL. One run of each call below; not a calibration |
| Deployed host, managed database, 2026-10-11 | Through `POST /chat` with live `hackathon-chat`, live retrieval, `JEV_MODE=fake`: a routine order was placed at $13.00 and an allergy mention went to staff with no order. The two-party-tray call did **not** reach the gate in two attempts, because the model asked for a callback number instead of proposing. The prompt was then changed; the changed prompt returned the correct proposal three times out of three when called directly against the live model, but it has **not** been redeployed or re-run on the host |
| Phone call through Twilio, deployed host, 2026-10-11 | One real call placed a routine order by voice (1 party tray, $60.00), with live `hackathon-chat` and `JEV_MODE=fake`. A team member made the call; the order and the caller's number then appeared on the dashboard. A flagged call by phone has **not** been run. Twilio webhook signatures are **not** validated (see the status table) |
| Live Jev with orders, deployed host, 2026-10-11 | One routine order through `POST /chat` with `JEV_MODE=live`: three `NO_CONCERN` answers from `jev-1.13.0` (confidence 100%, 98%, 94%), order placed at $13.00, case page labelled "Jev: Live". A manipulation attempt and a phone call with live Jev have **not** been run |

Local rehearsal calls:

| Call | Outcome |
|---|---|
| "A grilled pork banh mi and an iced coffee", then "It's for Sam", then yes | Read back at $13.00; order placed |
| "Two party trays for an office lunch, the name is Dana" | Read back at $120.00; callback line; `ORDER_OVER_LIMIT`; no order until staff approved it on the dashboard, which placed it |
| "One tofu banh mi for Lee. I have a peanut allergy" | Callback line; `KEYWORD_RULE` (allergy, peanut); denied on the dashboard; no order |
| The same allergy call again | Also `SIMILAR_TO_DENIED`, risk high (similarity 1.00: identical wording, so this shows the loop, not semantic recall) |
| "How much is the lemongrass chicken?", then "Okay, two of those and a cold cut special for Ravi" | Price answered from the menu; read back at $26.00; order placed. Nearest denied precedent scored 0.43 |
| "Can I get a pepperoni pizza for Kim?" | The model declined and listed the menu; no proposal reached the gate |

Chat replies took 1.6 to 6.6 seconds; confirmations took about 0.6 seconds.
The rehearsal found two confirmation bugs, both fixed with tests: an order that
opened with "okay" or "sure" was mistaken for a stray yes, and "yes, and add a
coffee" would have placed the order without the coffee.

Known limits:

- Allergy detection is a word list. "I can't eat nuts" does not match it.
- One item type repeated is the only way to order several; there are no
  modifiers ("no cilantro"), pickup times or payment.
- Some module and table names still say "booking" (`booking_chat.py`,
  `pending_booking_proposals`, `business_policies.booking_rules`).

The managed database holds rows from that check: one placed order (Rehearsal Sam)
and one pending review (Rehearsal Lee).

To move a host over, or to ship a later build (changes the live demo; run by the
owner; `seed --reset` is only needed once):

```bash
bash deploy/deploy_linode.sh apply     # ships this build and applies 006_orders.sql
ssh -i ~/.ssh/id_ed25519 root@172.232.10.159 \
  'cd /opt/ssa && docker compose -f deploy/compose.yaml run --rm init python -m backend.scripts.seed --reset'
```

Until `seed --reset` runs there, the stored rules are the salon's and every
confirmed order answers "I couldn't check that request", with nothing placed.

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

The repeatable seed inserts one server-configured **Demo Banh Mi** shop, its
order rules (a $100 total limit and an "Allergy mention" keyword rule) and a
six-item synthetic menu with prices (SSA-031). Reruns preserve existing rules and
menu edits, and fill an empty rules record when upgrading an older database.
`python -m backend.scripts.seed --reset` restores the preloaded example: it
overwrites the rules, updates the listed items and deactivates any other item
(such as the earlier salon services). It deletes nothing. No precedents, calls or
orders are fabricated by seeding.

To restore the preloaded menu and rules on an existing database:

```bash
docker compose run --rm init python -m backend.scripts.seed --reset
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

**Request board (issue #38).** The top bar reads "Incoming Request Logs - <shop
name>". The page shows the latest 100 calls in three columns and refreshes every
five seconds from `/dashboard/board`:

| Column | Colour | A call is here when | Card shows |
|---|---|---|---|
| Requests in progress | Blue | It has no outcome yet, is not marked ended, and had activity in the last 10 minutes | Caller name or number and "In progress"; not clickable |
| Fulfilled requests | Green | An order record exists | Caller and "Order placed: items · total" |
| Fulfilled requests | Grey | Staff denied it | "Declined by staff. No order placed." |
| Needs action | Red | A staff review is pending, or an approved or allowed order failed to execute | Caller and the reasons that sent it to staff |

A phone call is marked ended when its line closes, and an ended call with no
outcome leaves the board at once. Text and API calls are never marked ended, so
for them "in progress" is inferred: a call with no outcome leaves the board
after 10 minutes without a new transcript line. The
caller number is the one Twilio reports for phone calls; text and API calls have
none, so the card shows the name the caller gave, or "Unknown caller". Neither is
verified.

Green, grey and red cards link to the case page by action ID. It shows, in
order: caller information, the result (with Approve / Deny while pending), why
the case needs attention, the rules that took effect, the assessment (Jev and
similar past calls, each labelled fake or live), and the transcript.

**Settings (`/dashboard/settings`).** Staff change the shop name and edit the
menu and rules as one JSON document:

```json
{"menu": {"tofu": {"name": "Tofu banh mi", "price": 8.0}},
 "rules": {"max_order_total_auto": 100.0,
           "keyword_rules": [{"label": "Allergy mention", "keywords": ["allergy"]}]}}
```

The server validates the whole document (valid JSON, exactly `menu` and `rules`,
item codes in lowercase letters, digits and underscores, prices from 0 to 10000,
and the same rule checks the gate applies) and saves nothing if any part fails.
A saved change applies to the next call: the name appears in the top bar and the
phone greeting, and the receptionist and gate read the new menu and rules. An
item left out of the menu is deactivated, not deleted. "Restore banh mi example"
does what `seed --reset` does. Saves are JSON POSTs behind the dashboard
password, like staff decisions.

Verified: the offline tests and the PostgreSQL tests (board query, save, gate
reading the saved menu, restore), and a local run against PostgreSQL with
synthetic cases. On the deployed host (2026-10-11) the board, a fulfilled case, a
pending case and the settings page loaded with the stored calls; saving settings
was not exercised there.

Case evidence follows the SSA-010 handoff: `policy_snapshot.checks`,
`policy_snapshot.precedents`, `policy_snapshot.providers`, and the `JEV_`
entries in `reasons`. Clarification cases show absent providers as not checked.
Chat mode is not recorded in that contract, so it is explicitly shown as such.
Provider modes are historical evidence, not proof of a successful live call.

To try the UI without a database, set `DASHBOARD_PREVIEW=true` and restart the
server. This is an explicit, synthetic, read-only preview with no database access.
It includes a pending review, an in-progress call and a clarification case; the
last is reachable only by its case URL. Turn preview off before database integration. Database errors
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
- `create_action(business_id, interaction_id, payload, action_type='place_order')`
- `record_decision(business_id, action_id, decision, risk_level, reasons, policy_snapshot)`

Save text before calling the embedding provider so a provider failure cannot erase
the transcript. Attachment returns `True` for a new embedding, `False` if already
attached; it preserves the first vector/model. Vectors have 1,024 dimensions.
`search_similar` searches raw transcripts. SSA-008 uses `PrecedentService` and
`search_precedents` to retrieve only staff-decided calls; see
[retrieval and replay](docs/ssa-008-precedents.md).

An action starts as `proposed` with a generated `idempotency_key`. Recording a
decision stores gate evidence without changing state or executing anything.
Payloads follow the order proposal in the spec. The receptionist and gate lanes
own proposal validation and workflow orchestration.

By default, repository methods each use a connection transaction. To compose
several calls inside a service-owned transaction, pass
`connection_factory=lambda: contextlib.nullcontext(conn)` using that transaction's
connection. Helpers do not explicitly commit it.

Use the existing schema vocabulary: channels `phone`, `web`, `api`; states
`proposed`, `allowed`, `pending_review`, `rejected`, `executed`, `failed`;
decisions `allow`, `escalate`, `clarify`; review statuses `pending`, `approved`, `denied`.

Menu columns are `services.code/name/price_usd/active`. Placed orders are
`orders.action_id/items/total_usd/customer_name/callback_number` (`006_orders.sql`),
plus IDs and business IDs; `orders.action_id` is unique. Execution belongs to
`backend/connectors/orders.py`; the table alone does not enforce workflow
authorization. `slots` and `bookings` remain from the salon version and are no
longer written.

## Gate (SSA-010)

`backend.services.gateway.evaluate(action_id)` decides one `proposed` order and
returns a `GateDecision` (`decision`, `risk_level`, `reasons`, `policy_snapshot`,
plus `to_dict()`) in the spec's Demo scope shape. It places nothing. In one
transaction it writes one `security_decisions` row and moves the action:

| Decision | Action state | Also written |
|---|---|---|
| `allow` | `allowed` | — |
| `escalate` | `pending_review` | a `pending` `human_reviews` row; `interactions.status = 'escalated'` |
| `clarify` | `rejected` | — |

Calling it again for a decided action returns the stored decision and writes nothing.

Sequence: rules (`backend/policy/order_rules.py`), then precedent, then Jev. A
`clarify` stops after the rules and calls no provider. The rules read every limit
from the stored rules record on each decision:

- an item that is not an active menu code, or no items: `clarify` with
  `UNKNOWN_ITEM` or `EMPTY_ORDER`;
- a server-computed total above `max_order_total_auto`: `escalate` with
  `ORDER_OVER_LIMIT` (a total equal to the limit passes);
- any keyword of a keyword rule in the caller's own words (whole word or phrase,
  case-insensitive): `escalate` with `KEYWORD_RULE` and the rule's label.

A missing or malformed rules record raises an error; it is never read as "no
limits". Keyword matching is a word list and will miss unusual phrasings.

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
| Order connector (SSA-031, replacing the SSA-005 calendar) | Implemented (`backend/connectors/orders.py`); verified by integration tests on local PostgreSQL |
| Offline inference tests | Explicit fake clients |
| Akamai adapter | Reused; live calls skipped |
| Managed database and Linode deployment (SSA-021) | Deployed 2026-10-10 at `https://172-232-10-159.sslip.io`; HTTPS health, managed database and booking-after-restart verified; Twilio check pending. See the section below |
| Gate (SSA-010) | Rules, precedent retrieval and persistence integrated; fake/live modes labelled; one live retrieval rehearsal run on the deployed host (see SSA-021 section) |
| Precedent (SSA-008) | Real pgvector and application replay tested with fake inference; live recall rehearsed once on the deployed host; recordings and threshold calibration pending |
| Jev (SSA-009) | Live through the TypeSafe SDK when `JEV_MODE=live` and a key is set; three checks per request; five live scenarios and two local app flows verified on 2026-10-11 |
| Receptionist (SSA-014, SSA-031) | Wired to the real gate and order connector; live chat rehearsed locally for orders on 2026-10-11 (see the SSA-031 section) |
| Dashboard (SSA-015) | See the SSA-015 section above |
| Voice (SSA-023) | Twilio webhook and ConversationRelay WebSocket on the deployed host; one real call placed an order on 2026-10-11. A flagged call by phone is untested. Twilio's `X-Twilio-Signature` is **not** validated, so anyone who knows the URL can open a fake call; such a call still goes through the same gate and staff review. A chat model call is allowed 30 seconds and a Jev call 8 seconds; the caller hears a "one moment" line while waiting. The browser microphone (SSA-030) is not on `main` |

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
| Twilio reaches `wss://…/voice/stream` | One real call on 2026-10-11 placed an order by voice (see the SSA-031 verification table) |
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

## Testing the order connector (SSA-031)

`backend/connectors/orders.py` replaces the SSA-005 booking calendar.
`execute(action_id)` validates the action (`place_order`, and `allowed` or
`pending_review` with an `approved` review), prices the items from the current
menu, and writes one `orders` row in the same transaction that marks the action
`executed`.

```bash
docker compose up -d db
export TEST_DATABASE_URL="postgresql://demo:demo@localhost:5432/demo"
pytest tests/integration/test_orders.py -v
```

What the tests verify:

- **Idempotency:** executing the same action twice yields exactly one order row.
- **Server pricing:** the total and per-item prices come from the menu; a price
  or total in the payload is ignored.
- **State gate:** actions that are `proposed`, `rejected`, awaiting review, or of
  the old `book_appointment` type cannot be executed.
- **Menu changes:** an item removed after the gate decided fails cleanly with
  state `failed` and no order.

The paragraph below records the original SSA-001 rebuild and predates the order pivot.

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
the order connector (SSA-031); tests may still inject their own. Set
`RETRIEVAL_MODE` and `JEV_MODE`, or every placeable order goes to staff review.
A spoken yes such as "yeah" or "yes, that's correct" confirms; a reply containing
a change ("yes, but…") clears the proposal. Each item the model sends is matched
to an active menu item by code or display name and stored as its code; the
read-back total is computed by the application from menu prices. No live
Akamai chat call has been verified yet.
See [SSA-014 integration guide](docs/ssa-014-integration.md) for wiring, tests,
and safe fallback behavior.
