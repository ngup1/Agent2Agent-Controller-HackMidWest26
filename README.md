# Supervised Service Agent

**An AI phone receptionist that can't place an order on its own say-so.** Built at Hack Midwest 2026.

A caller, or another AI agent, orders from a banh mi shop by voice or text. The AI takes the order, but a separate gate decides what happens: routine orders are placed, risky ones go to staff with a promised callback. Every staff decision becomes precedent for the next similar call.

**Live demo:** call **+1 (229) 490-2249**, or open <https://172-232-10-159.sslip.io/dashboard> (asks for the staff password)

user: staff
pass: 123

## How it works

```mermaid
sequenceDiagram
    actor Caller
    participant Twilio
    participant R as AI receptionist
    participant G as Gate
    participant DB as PostgreSQL + pgvector
    participant Jev
    actor Staff

    Caller->>Twilio: Phone call
    Twilio->>R: Transcribed speech
    R->>Caller: Reads the order back
    Caller->>R: "Yes"
    R->>G: Proposed order
    G->>DB: 1. Shop rules (menu, $100 limit, allergy words)
    G->>DB: 2. Similar past calls staff decided
    G->>Jev: 3. Override, false authority, intent mismatch
    alt allow
        G->>DB: Place the order
        R->>Caller: "Your order is placed"
    else escalate
        G->>Staff: Review on the dashboard
        R->>Caller: "Staff will call you back"
        Staff->>DB: Approve (places the order) or deny
        Twilio->>Caller: Callback confirming the approved order
    else clarify
        R->>Caller: Asks again
    end
```

- **The model only proposes.** It never prices, approves or places an order. The server computes totals from the stored menu.
- **The gate fails safe.** If retrieval or Jev is unavailable, the order goes to staff. A fake result is never substituted for a live failure.
- **Staff stay in control.** The dashboard shows each call in one of three columns (in progress, fulfilled, needs action) with the evidence; staff approve or deny.
- **Approval calls back.** When staff approve, Twilio phones the customer to confirm the order.
- **The shop learns.** A request similar to one staff denied is flagged as high risk next time.
- **Rules are editable.** Staff change the menu and rules on the settings page; the next call uses them.

## Built with

| Part | Technology |
|---|---|
| Voice | Twilio ConversationRelay (inbound), Twilio Voice (outbound callback) |
| Receptionist and embeddings | Akamai-hosted models (`hackathon-chat`, `hackathon-embedding`) |
| Supervisor | Jev through the TypeSafe SDK |
| App and dashboard | FastAPI, Jinja2 |
| Data and precedent search | PostgreSQL 17 with pgvector (Akamai Managed PostgreSQL) |
| Hosting | Akamai Linode behind Caddy (HTTPS and WSS) |

## Setup

Needs Docker with Compose.

```bash
git clone https://github.com/ngup1/Agent2Agent-Controller-HackMidWest26.git
cd Agent2Agent-Controller-HackMidWest26
cp .env.example .env          # then fill in the values below
docker compose up --build     # database, migrations, seed, app
curl --fail http://localhost:8000/healthz   # {"status":"ok","database":"reachable"}
```

Values to set in `.env`:

| Variable | Needed for |
|---|---|
| `DASHBOARD_PASSWORD` | The dashboard (it returns 503 until set) |
| `OPENAI_API_KEY` | The receptionist: the Akamai workshop key |
| `CHAT_SESSION_SECRET` | `POST /chat`: any random string of 24+ characters |
| `TYPESAFE_API_KEY` and `JEV_MODE=live` | Live Jev (default is `fake`) |
| `RETRIEVAL_MODE=live` | Live embeddings for precedent search (default is `fake`) |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER` | The callback after staff approve |

Open <http://localhost:8000/dashboard> (any username, your password). Then let a customer agent order from the receptionist over `POST /chat`:

```bash
pip install -r requirements.txt
python -m backend.scripts.agent2agent --scenario pork --phone <your-number>
# other scenarios: large (over the $100 limit), allergy, manipulation
```

Escalated orders appear under **Needs action**; open the card and approve or deny.

Useful commands:

```bash
docker compose run --rm init python -m backend.scripts.seed --reset   # restore example menu and rules
python -m pytest -q -m 'not integration'                              # tests without a database
TEST_DATABASE_URL=postgresql://demo:demo@localhost:5432/demo python -m pytest -q   # with the database tests
```

Inference is fake in every test. Host commands need Python 3.11+.

**Phone calls** need a public HTTPS host. Deploy to Akamai (needs a configured `linode-cli` and an SSH key), then point your Twilio number's "a call comes in" webhook at `https://<host>/voice/incoming`:

```bash
bash deploy/deploy_linode.sh plan    # read-only: shows what would be created and the price
bash deploy/deploy_linode.sh env     # write the managed DATABASE_URL into .env.deploy
bash deploy/deploy_linode.sh apply   # create or reuse the Linode (billed), deploy, wait for HTTPS
```

## More detail

- [Engineering notes](docs/engineering-notes.md): verification record, gate internals, deployment
- [Product specification](spec.md)
