# Supervised Customer-Service Agent — MVP

**Version:** 0.2 · **Purpose:** Plan eight small GitHub issues, then build a working demo.

> A customer-service agent proposes a mock booking. The application retrieves similar reviewed cases, asks Jev whether the action needs review, and either executes it or waits for a human to approve or reject it.

This replaces the previous, larger specification. Build the loop above—not a production platform. The companion [teaching guide](TEACHING_GUIDE.md) explains the implementation without adding requirements.

## 1. Instructions for Codex

For the first session, **create the issue backlog only**. Do not implement the app, provision cloud services, or make paid API calls yet.

Read this file and turn Section 10 into **eight issues**, each containing an outcome, implementation tasks, acceptance criteria, and dependencies. Keep a local copy in `docs/issues.md`. Add `<!-- mvp-issue: MVP-001 -->` to each issue body, using its corresponding number.

Verify the current repository and GitHub destination before publishing. The Akamai workshop is a reference, not the destination. Reuse issues with matching MVP markers rather than creating duplicates. Do not automatically close or rewrite any earlier SSA issues. If GitHub access is unavailable, finish the local drafts and report that blocker.

**Do not create expansion issues unless explicitly requested.** No elaborate labels, milestones, issue-map files, or additional planning documents are needed.

In later implementation sessions, work on the requested issue. Prefer ordinary functions and one application over frameworks or abstractions that this MVP does not need.

## 2. What we are building

Use **one fictional salon**, carried over as a provisional demo choice. Use synthetic names, a short service list, and a few fixed demo appointment slots. This is not a real scheduling system.

The demo has two pages:

- **Customer page:** Type requests, see replies, and refresh the result of a pending request.
- **Reviewer page:** See proposed actions, retrieved examples, Jev's result, and Approve/Reject buttons. Also show recent outcomes and mock bookings.

The only business action is `create_booking`. The agent can also answer questions from a small set of public business facts or ask for missing details.

**Scope changes from the previous version:** Text first; phone calls move to proposed expansions. No application authentication, roles, customer verification, multi-tenancy, worker processes, approval expiry/versioning, caches, performance tuning, or large evaluation suite. The cloud default is one Linode running the app and PostgreSQL together; a separate managed database is optional.

No payments, refunds, cancellation, real customer records, live calendar writes, unrestricted tools, or automatic model training. No configurable policy editor or incident-management interface.

Because the reviewer page has no login, this is a **team-controlled demo, not a public customer deployment**. Anyone with access to its review endpoints can approve requests. Use synthetic data and keep the demo local or access the Linode through an SSH tunnel. API/database credentials still belong in environment variables, never in Git.

## 3. Smallest architecture

```text
Customer browser
       |
       v
FastAPI app ----> Conversational model: reply or propose a booking
       |
       +-------> PostgreSQL + pgvector: retrieve similar reviewed cases
       |
       +-------> Jev: assess the proposed booking and its context
       |
       +---- ALLOW ----------> Insert a mock booking
       |
       +---- REVIEW ---------> Save pending action
                                  |
                            Reviewer browser
                                  |
                         Approve / Reject
```

Use Python, FastAPI, Pydantic, plain HTML with a little JavaScript, PostgreSQL/pgvector, and Docker Compose. A few direct database queries through psycopg are sufficient. One SQL initialization file is enough; no migration framework is required yet.

Use simple functions for chat, embeddings, and Jev calls. Prefer the workshop chat/embedding service when team access is available. Keep provider URLs, model names, and keys configurable. Use the documented TypeSafe API/SDK for Jev; do not invent SDK methods or model identifiers.

An explicit, visibly labeled mock-provider mode is useful for development. Tests should stub external calls. Never silently substitute a fake result when a real provider fails, and never call a mock run a live Jev demonstration.

## 4. End-to-end behavior

### 4.1 Conversation and proposal

Give the conversational model the public service list, demo slots, and recent messages—not all bookings or unrestricted database access.

Each turn produces either a normal reply or one structured proposal:

```json
{
  "tool_name": "create_booking",
  "arguments": {
    "service_id": "haircut",
    "slot_id": "slot_1",
    "customer_name": "Taylor",
    "party_size": 1
  }
}
```

Validate the shape with Pydantic. Reject unknown tools and extra authority fields such as `approved`. Ask for missing details rather than inventing them. Validate service/slot IDs against the demo fixtures and allow party sizes 1–3 only. Treat invalid proposals as rejected; do not send them to the booking function.

There is no separate customer-confirmation protocol in this version. Complete requests can proceed to checking. Adding an explicit confirmation step is a proposed expansion.

### 4.2 Retrieve relevant examples

For every valid booking proposal, build an embedding input containing the proposed action and relevant customer messages. Retrieve the **three nearest reviewed cases** from pgvector using the same embedding model used to seed the cases. Exact similarity search is sufficient; no vector-index tuning.

Seed **six synthetic cases**: two examples of abuse or instruction manipulation, two legitimate exceptions, and two benign requests/false positives. Each includes a summary, label, and reviewer note. Show retrieved cases in the reviewer page.

Similarity is supporting context, not a maliciousness score. No matches does not mean safe. A failed retrieval is an error, not a successful empty search.

### 4.3 Ask Jev and route in code

Send Jev the booking proposal, relevant messages, applicable demo rules, and retrieved examples. Ask a focused question: **does this proposed action need human review because of instruction manipulation, unsupported authority claims, ambiguity, or a mismatch with the customer's request?**

Normalize the result inside our app to `ALLOW` or `REVIEW`. These are our internal labels, not assumed vendor response fields. An unclear or unrecognized result becomes `REVIEW`. Show Jev's actual returned decision/evidence when available; do not invent a generated explanation or introduce confidence-threshold tuning.

The application then applies this small rule table:

| Condition | Result |
|---|---|
| Invalid tool, fields, service, slot, or party size | Reject; no booking |
| Valid proposal but retrieval/Jev fails or returns uncertainty | Human review |
| Valid group booking for 2–3 people | Human review regardless of Jev |
| Jev flags a valid proposal | Human review |
| Valid single-person proposal, successful retrieval, and Jev allows | Execute mock booking |

Still run retrieval and Jev for group bookings so reviewers can see their evidence. The group rule provides a predictable way to demonstrate escalation even when model judgments vary.

**Every booking must pass through this function.** The conversational model never directly calls the booking writer. Ordinary FAQ/clarification replies are not screened in the MVP; this is action supervision, not complete protection of everything the chatbot says.

### 4.4 Human review and execution

Save pending actions instead of keeping an HTTP request open while waiting. Return “This request needs review. Nothing has been booked yet.” The reviewer approves or rejects the exact stored payload, optionally adding a note.

Approval runs the same mock booking writer used by the automatic path. Recheck that the action is still pending and the payload remains valid. Rejection creates no booking. Do not rerun Jev after approval and create an endless review loop.

Do not support editing pending requests. While a conversation has a pending action, disable new submissions and have the server return its pending status instead of generating another proposal. A reviewer must resolve it before that conversation continues.

Use four persisted statuses: `pending_review`, `executed`, `rejected`, and `failed`. Store the booking and the action's final status in one database transaction. A unique `bookings.action_id` plus a pending-status check prevents repeated approval of that same action from creating another booking. No distributed execution or retry system is required.

The mock writer just records bookings; it does **not** model real availability, capacity, payments, or vendor integrations. Do not promise those capabilities in the UI.

Return booking confirmations from the saved result, not the model's draft. On provider failure, show the actual missing stage. If the database is unavailable, show an error and do not claim a booking or saved review exists. Pending reviews have no timeout or automatic approval in this version.

## 5. Minimal storage and routes

Four tables are enough. Combine related details in JSONB rather than building a large normalized schema.

| Table | Store |
|---|---|
| `conversations` | ID and message history as JSONB |
| `actions` | ID, conversation ID, proposal, status, assessment/retrieved-case snapshot, optional reviewer decision/note, timestamps |
| `bookings` | ID, unique action ID, booking payload, created time |
| `incidents` | ID, summary, label, reviewer note, embedding |

The incident corpus is seeded, not automatically updated from reviews. A rejection is not proof of maliciousness, and an approval is not a permanent policy exception.

| Route | Purpose |
|---|---|
| `GET /` | Customer page |
| `GET /review` | Reviewer page |
| `POST /api/chat` | Accept message and optional conversation ID; return reply/action status |
| `GET /api/conversations/{id}` | Refresh that demo conversation and its actions |
| `GET /api/actions` | Show actions and their linked booking results |
| `POST /api/actions/{id}/review` | Approve or reject a pending action |
| `GET /health` | Basic application health |

No login routes or public “execute arbitrary action” endpoint. Review APIs are deliberately unauthenticated for this controlled demo. Use normal escaped HTML/text rendering; there is no need to render model output as executable HTML.

Suggested files—not a requirement to create a framework:

```text
app/
  main.py             # HTTP routes and page rendering
  chat.py             # Conversational model and proposal parsing
  supervisor.py       # Jev call and normalized result
  memory.py           # Embed text and retrieve cases
  workflow.py         # Checks, routing, review, mock execution
  db.py               # Database connection and queries
  schemas.py          # Proposal and request models
  seed.py             # Initialize demo records and embed cases
  templates/          # Customer and reviewer pages
  static/             # Small JavaScript and CSS files
schema.sql
data/                  # Public business facts and six seed cases
tests/
Dockerfile
compose.yaml
.env.example
README.md
spec.md
TEACHING_GUIDE.md
```

## 6. Akamai deployment

First run the app and pgvector-enabled PostgreSQL locally with Docker Compose and a persistent database volume. Then run the **same two containers on one Akamai Linode**. Bind the app to localhost and use an SSH tunnel for the unauthenticated team demo; do not publish the database port.

This deliberately avoids provisioning a separate managed database for the first build. Akamai Managed PostgreSQL remains an optional follow-on using the database guide. Do not add Object Storage, Valkey, Kubernetes, or GPUs to the MVP.

Adapt the deployment approach from `docs/03_body.md`, retrieval ideas from `docs/02_memory.md`, and provider settings from `docs/inference.md`. Consult the actual guides during implementation; account access and provider interfaces are external prerequisites. Confirm sponsor eligibility with the organizers rather than assuming a particular deployment qualifies.

The cloud demonstration should complete one checked action and show its saved record—not only a health endpoint. Restarting the app must not erase saved actions or bookings. Document manual deployment and cleanup; no infrastructure-as-code system is needed.

## 7. Definition of done

The core works when a teammate can:

1. Start the app/database, seed the demo, and open both pages without logging in.
2. Send a complete single-person request; with an allowing assessment, see a saved mock booking.
3. Submit a two-person request, see retrieved cases and Jev's result, then approve it to create one booking.
4. Reject another pending request and verify no booking was created.
5. See an unsupported proposal blocked and a retrieval/Jev failure held for review rather than auto-executed.
6. Demonstrate the same loop on Akamai with real configured chat, embeddings, and Jev, clearly labeling the business backend as mock.

Use a small pytest suite for the rule/decision branches, approve/reject behavior, and repeated approval. Stub external calls for these tests. Separately run a few live smoke requests. Semantic judgments can vary; record live outcomes rather than claiming guaranteed detection. Missing live credentials or cloud access is a reported blocker, not a successful live demo.

No load tests, latency targets, 60-case benchmark, or ablation study is required yet.

## 8. Teaching and setup documentation

Keep `TEACHING_GUIDE.md` as the human explanation. Keep `README.md` operational: prerequisites, environment variables, start/seed/test commands, demo steps, and Linode access/cleanup.

Document database configuration, chat and embedding provider settings, Jev credentials, and the explicit mock-provider switch. Use placeholders in `.env.example` and ignore `.env` in Git. Do not add unrelated configuration flags.

The guide is not permission to implement its proposed expansions.

## 9. Proposed expansions — not MVP requirements

| Expansion | What it would add |
|---|---|
| **Phone calls** | Twilio ConversationRelay feeds transcribed speech into the same conversation function and speaks its returned text. Add secure call endpoints, waiting behavior, and hangup handling then—not now. |
| **Save reviewed cases** | Let a reviewer explicitly write/label a useful case, embed it, and add it to retrieval. Do not automatically label every rejected request malicious. |
| **Customer confirmation** | Show exact booking details and require confirmation before a write; support changes and cancellations of pending requests. |
| **Real business connector** | Replace the mock writer with one sandbox scheduling/POS integration and its actual availability rules. |
| **Authentication and safe public deployment** | Add reviewer login, access controls, customer boundaries, HTTPS, limits, and appropriate data handling before public or real-customer use. |
| **Phone/staff notifications** | Notify a reviewer, transfer a call, or define follow-up when nobody responds. |
| **Managed PostgreSQL** | Move the container database to Akamai's managed service once the loop is working. |
| **More rules and visibility** | Editable business rules, a pause control, response screening, and clearer decision history. |
| **Measure and improve supervision** | Compare Jev with/without incident retrieval; examine false positives and missed unsafe actions before tuning. |
| **Reliability and scale** | Add retries, reconciliation, workers, caching, vector indexes, or multiple instances only when a real need appears. |

## 10. Eight implementation issues

Use these as the complete initial backlog. Include the listed checks in each issue; do not split them into a large security/platform program.

### MVP-001 — Start the app and database

**Depends on:** None.

Create the Python/FastAPI project, minimal dependencies, Docker Compose app/database services, `.env.example`, and ignored secrets. Define the shared proposal schema.

**Acceptance:** App and database start locally; `/health` responds; environment setup is documented; no login or worker exists.

### MVP-002 — Add demo data and the mock booking writer

**Depends on:** MVP-001.

Create the four tables, public service/slot fixtures, seed command, and booking/action persistence functions.

**Acceptance:** Fixtures load; a test booking and action persist; invalid service/slot/party fields are rejected; repeat execution for one action cannot add another booking.

### MVP-003 — Retrieve similar reviewed cases

**Depends on:** MVP-002.

Seed six labeled synthetic cases, embed them, and implement top-three pgvector retrieval with provider configuration.

**Acceptance:** A query returns case summaries and labels; stored/query embeddings use the same model; retrieval errors are distinguishable from zero results; no cache or index tuning is added.

### MVP-004 — Turn chat into replies or proposals

**Depends on:** MVP-001, MVP-002.

Implement the conversational model call with public facts and conversation history. Parse a reply or one booking proposal.

**Acceptance:** Missing details produce a question; complete requests produce a validated proposal; unsupported/authority fields are rejected; this module cannot execute bookings.

### MVP-005 — Add Jev and the mandatory action check

**Depends on:** MVP-002, MVP-003, MVP-004.

Implement Jev's assessment call and the Section 4 routing table. Persist the proposal, retrieved evidence, decision, and actual result.

**Acceptance:** Valid one-person allow path executes; group/concern/uncertainty paths remain pending; provider failure cannot auto-allow; mocked tests cover each route and are labeled as mocks.

### MVP-006 — Resolve pending actions

**Depends on:** MVP-005.

Add list/detail data and approve/reject behavior using the saved action. Store optional reviewer notes and expose results to the customer page.

**Acceptance:** Pending means no booking; approval executes once; rejection never executes; repeated or conflicting decisions do not re-execute; new chat submissions cannot replace a pending action.

### MVP-007 — Build the two-page demo

**Depends on:** MVP-004, MVP-005, MVP-006.

Add the customer chat and reviewer pages with simple refresh controls. Show action payloads, retrieved cases, assessment status, decisions, and saved mock bookings.

**Acceptance:** A teammate completes allow/approve/reject flows through the UI; pending is never labeled booked; mock/live provider modes and the unauthenticated-demo warning are visible.

### MVP-008 — Smoke-test, document, and deploy on Akamai

**Depends on:** MVP-001 through MVP-007.

Finish the small regression suite, README, and demo script. Deploy the same containers to one Linode when team access is available. Keep the teaching guide aligned with the actual code.

**Acceptance:** Section 7's flows work; app restart preserves data; local/cloud instructions and cleanup are documented; real versus mocked results and missing prerequisites are reported accurately.

## Reference links

These are implementation references carried over from the previous specification, not extra scope. Check their current interfaces when writing integrations.

- [Akamai workshop](https://github.com/akamai-developers/akamai-workshop-hackmidwest)
- [PostgreSQL/vector guide](https://github.com/akamai-developers/akamai-workshop-hackmidwest/blob/main/docs/02_memory.md)
- [Linode deployment guide](https://github.com/akamai-developers/akamai-workshop-hackmidwest/blob/main/docs/03_body.md)
- [Workshop inference guide](https://github.com/akamai-developers/akamai-workshop-hackmidwest/blob/main/docs/inference.md)
- [TypeSafe quickstart](https://docs.typesafe.ai/introduction/quickstart)
- [Future phone integration reference](https://www.twilio.com/docs/voice/conversationrelay/onboarding)

**Stop the initial Codex session after creating the eight issue drafts/issues. Implement only in a later requested session.**
