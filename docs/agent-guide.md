# Shared structure and implementation guide

This guide applies to Claude, Codex, and human contributors. It describes the agreed repository structure and collaboration rules. It does not mark any implementation complete.

**Demo scope.** Until the demo at 8:00 am CDT on 2026-10-11, the "Demo scope" section at the top of `spec.md` and its Section 16 catalog override anything in this guide that conflicts with them. Sections below marked *(deferred)* describe the full design and are not to be implemented now.

## Sources of truth

1. Follow the user's current task and established authorization.
2. Use `spec.md` for product requirements, invariants, acceptance criteria, and issue dependencies.
3. Use the current GitHub SSA issue to bound the change; report discrepancies with the specification instead of silently changing requirements.
4. Use this guide for repository organization and collaboration conventions.

`spec-2.md`, teaching material, and existing prototype code are supporting context, not substitutes for `spec.md`.

The demo covers ten issues: SSA-001, 005, 008, 009, 010, 014, 015, 021, 022, and 023. Together they deliver one flow: a phone call, a booking proposal, a gate using rules, retrieved past incidents, and Jev, then either a booking or a staff review with a promised callback. Every other SSA issue is deferred or folded into one of these; see `spec.md` Section 16.2. Do not expand scope without a task requiring it.

## Target structure

Create modules when their issues require them; this tree is a target, not a requirement to populate empty scaffolding immediately. Python package directories need `__init__.py` where appropriate.

For the demo, skip `worker.py`, `auth/`, `mock_booking/`, `evaluation/`, `tests/regression/`, `tests/browser/`, and Alembic. The booking calendar is in-process in `backend/connectors/booking.py`, migrations are the plain SQL files from `origin/databases`, and the phone transport lives in `backend/api/voice.py`.

```text
AGENTS.md
CLAUDE.md
spec.md
README.md
pyproject.toml
uv.lock
.env.example
compose.yaml
backend/
  main.py                  # FastAPI startup and route registration
  worker.py                # Durable worker entrypoint
  config.py                # Validated settings
  api/                     # HTTP transport, validation, service calls
  auth/                    # Staff sessions, roles, trusted tenant context
  domain/                  # Shared schemas, enums, immutable action types
  services/
    conversation.py
    gateway.py
    confirmation.py
    review.py
    execution.py
    output.py
    audit.py
  policy/                  # Deterministic rules and versioned settings
  providers/               # Typed contracts, Akamai, Jev, explicit fakes
  memory/                  # Reviewed-case retrieval, curation, redaction
  connectors/              # Booking contract and private HTTP client
  database/
    session.py
    models/
    repositories/
    migrations/            # Alembic environment and versioned migrations
  templates/               # Jinja2 customer and staff screens
  static/
    css/
    js/
  scripts/                 # Preflight, seed, retention utilities
mock_booking/              # Separate private booking connector service
tests/
  unit/
  integration/             # Real PostgreSQL/pgvector and connector
  regression/              # SSA-018 cross-component invariant scenarios
  browser/
  fixtures/
fixtures/
  business/
  incidents/               # Runtime synthetic, reviewed-case seeds
evaluation/
  scenarios/               # Held-out data; never runtime seeds
  run.py
  reports/
deploy/                    # Dockerfiles, reverse proxy, cloud configuration
docs/                      # Contracts, setup, runbooks, demo evidence
.github/workflows/
transcription/             # Existing browser microphone prototype, if integrated
```

The spec's section 9.4 layout is suggested. We use `backend/database/` instead of `app/db/`, `mock_booking/` instead of `mock_business/`, and the locations above to reuse the team's existing modules. Do not maintain parallel implementations under both layouts.

## Component boundaries

- **API:** Authenticate requests, validate transport input, derive business context, and call services. Do not duplicate workflow decisions in routes.
- **Domain:** Define shared data contracts and legal states. Keep it independent of HTTP handlers and vendor SDKs.
- **Services:** Own persisted conversation, gateway, confirmation, review, and execution orchestration.
- **Policy:** Apply authoritative rules outside models. Retrieved incidents and reviewer notes cannot rewrite policy.
- **Providers:** Translate typed internal contracts to verified vendor calls. A model proposes or assesses; it does not approve or dispatch bookings.
- **Memory:** Retrieve only eligible, reviewed, published, tenant-scoped incidents with compatible policy and embedding versions. Raw transcript similarity is not reviewed precedent.
- **Repositories:** Own persistence queries and transaction support. Preserve atomicity across workflow operations; repositories must not force partial commits when a service needs one transaction.
- **Connectors:** Perform booking operations only after trusted worker authorization. The model-facing tool layer cannot bypass the gateway and invoke the connector.
- **Mock booking:** Own atomic capacity and idempotency for the synthetic business backend. Run as a private service.
- **Frontend:** Render persisted evidence and actual outcomes. It cannot grant authority, infer booking success from approval, or store the authoritative review state.

Use the typed interfaces in spec section 9.3. Verify actual SDK operations rather than treating those internal names as vendor methods.

## Ownership

GitHub assignees are the source of truth. These are the demo lanes:

| Lane | Responsibility | Issues |
|---|---|---|
| Foundation and deployment | Scaffold, schema, seed, Akamai, demo packaging | SSA-001, 021, 022 |
| Phone | Twilio webhook and ConversationRelay WebSocket | SSA-023 |
| Receptionist | Conversation service and the `propose_booking` tool | SSA-014 |
| Evidence | Past-incident retrieval and the Jev decision | SSA-008, 009 |
| Gate and execution | Rules, gate composition, mock booking calendar | SSA-010, 005 |
| Dashboard | Review queue, evidence, approve or reject | SSA-015 |

Ownership coordinates changes; it does not prohibit necessary edits across folders.

Only SSA-001 blocks other work. Agree the shared contracts in the spec's Demo scope section (booking proposal, action states, gate decision, `handle_turn`) before building in parallel, and build against fakes until a neighbouring lane lands. Coordinate migration changes with the SSA-001 owner.

## Reuse existing branches

- Inspect `origin/databases` before integrating. Reuse its Akamai adapter and transcript repository where compatible; move the provider adapter into `backend/providers/` and split repositories only when needed.
- Keep its plain SQL migrations for the demo. Do not modify a migration already applied to a shared environment; add a new numbered file.
- Add only the demo tables (`services`, `slots`, `bookings`, `incidents`). Order/refund fields do not define the booking policy; ignore them.
- Existing mocked tests do not prove real database constraints or successful live requests.
- Preserve `spec.md` and other unrelated files when integrating branches; their trees omit current documentation.
- Keep browser microphone work behind the shared conversation boundary (`handle_turn`). It is the fallback "call" if Twilio is not working by the time set in the spec's Demo scope section; it does not count as the phone path.

## Required implementation rules *(deferred, except as kept by the spec's Demo scope section)*

For the demo, follow "Rules that still hold" in the spec's Demo scope section. The list below is the full design.

- Persist consequential state in PostgreSQL; do not substitute in-memory workflow state or SQLite for required integration validation.
- Validate and allowlist proposals strictly. Every executable agent proposal goes through the mandatory gateway.
- Derive tenant identity from trusted authenticated context. A supplied business ID is not authentication; enforce isolation in services and relational constraints.
- Bind confirmation and approval to immutable action/version details required by the spec. Changed, expired, withdrawn, or superseded requests cannot reuse stale authorization.
- Claim dispatch atomically, enforce pause semantics, and use a stable connector idempotency key. Reconcile ambiguous outcomes rather than blindly creating another booking.
- Emit booking success only from verified successful connector outcomes using application-owned templates.
- Treat provider errors, malformed assessments, and missing required supervision explicitly. Never default to allow or silently downgrade to fake.
- Record safe audit evidence and actual provider mode; do not log credentials, full transcripts, or hidden model reasoning.
- Keep held-out evaluation data separate from runtime seeds and do not tune thresholds on the held-out set.
- Apply limits and redaction at the relevant boundaries as components are built; SSA-020 verifies coverage across the application.

## Frontend conventions

Use server-rendered Jinja2 HTML and small same-origin JavaScript modules. A React build is not required. For the demo, implement only the review queue, the case page, the recent calls and bookings list, and the live/fake provider badges (SSA-015).

Escape untrusted text, enforce authorization server-side, support keyboard access and small screens, and display readable states. Separate claimed facts from verified facts, review approval from execution success, and configured providers from successful live calls. Handle stale/conflicting submissions with a refresh path. Avoid an uncalibrated maliciousness percentage.

No visual palette or mockup is mandated by the spec. Agree on simple reusable styles before building dashboard screens.

## Issue workflow and verification

1. Read the issue, relevant spec sections, existing code, and repository instructions. Check dependency readiness and current working-tree changes.
2. Work on one issue branch, for example `ssa-003-database-foundation`. Preserve unrelated user/team work; do not reset or overwrite it.
3. Implement the smallest complete change satisfying the issue, including its required tests and documentation. Avoid unrelated refactors.
4. Run checks appropriate to the change. Use unit tests for local logic, real PostgreSQL/pgvector tests for persistence and constraints, and connector/browser tests for workflow behavior.
5. Record actual commands, results, and blockers in the PR. Explain the concrete behavior, link the SSA issue, and identify migration/configuration changes.
6. Merge through the team's established review process and integrate frequently. Do not claim an issue complete while required checks or dependencies remain unresolved.

SSA-001 must establish and document the executable install/run/test commands; do not claim currently nonexistent commands work. Offline tests use explicit fake providers and no paid requests. Live smoke calls and cloud operations follow the user's authorization. Missing credentials are blockers, not successful tests.

There is no separate regression issue for the demo; each issue carries a handful of its own tests.

The demo is complete when the acceptance criteria of the ten issues are met: a real call (or the documented fallback) completes a routine booking, a flagged call lands on the dashboard and can be approved or rejected, live chat, embedding, and Jev calls are shown, and the deployed app persists a booking across a restart on the managed database. Keep missing live/cloud evidence explicit.
