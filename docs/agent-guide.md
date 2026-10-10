# Shared structure and implementation guide

This guide applies to Claude, Codex, and human contributors implementing P0. It describes the agreed repository structure and collaboration rules. It does not mark any implementation complete.

## Sources of truth

1. Follow the user's current task and established authorization.
2. Use `spec.md` for product requirements, invariants, acceptance criteria, and issue dependencies.
3. Use the current GitHub SSA issue to bound the change; report discrepancies with the specification instead of silently changing requirements.
4. Use this guide for repository organization and collaboration conventions.

`spec-2.md`, teaching material, and existing prototype code are supporting context, not substitutes for `spec.md`.

P0 covers SSA-001 through SSA-022: supervised text booking, staff review, reviewed-incident retrieval, durable execution, validation, and Akamai deployment. Phone work is P1; optional connectors/cache/storage are P2. Do not expand scope without a task requiring it.

## Target structure

Create modules when their issues require them; this tree is a target, not a requirement to populate empty scaffolding immediately. Python package directories need `__init__.py` where appropriate.

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

## Five-person ownership

| Person | Primary responsibility | Issues |
|---|---|---|
| 1 | Foundation, database, CI, deployment, integration coordination | SSA-001, 003, 021, 022 |
| 2 | Mock connector, immutable actions, confirmations, execution | SSA-005, 006, 011, 013 |
| 3 | Provider verification, embeddings, Jev, retrieval evaluation | SSA-002, 008, 009, 019 |
| 4 | Authentication, policy, gateway, limits/privacy; regression coordination | SSA-004, 007, 010, 020, 018 |
| 5 | Review, conversation, dashboard, incident curation, audit visibility | SSA-012, 014, 015, 016, 017 |

Everyone contributes component tests to SSA-018. Person 1 helps Person 5 with backend audit instrumentation. Ownership coordinates changes; it does not prohibit necessary edits across folders.

Agree on action schemas/states, trusted tenant context, API contracts, provider interfaces, and transaction boundaries before parallel implementation. Coordinate migration changes with Person 1 and domain/execution contracts with Person 2. Follow issue dependencies before treating an integration as complete.

## Reuse existing branches

- Inspect `origin/databases` before integrating. Reuse its Akamai adapter and transcript repository where compatible; move the provider adapter into `backend/providers/` and split repositories only when needed.
- Its create-if-missing SQL is prototype groundwork. Introduce versioned migrations without assuming existing databases can be safely overwritten. Do not modify migrations already applied to a shared environment; add a new migration.
- Extend existing governance tables for immutable versions, confirmations, authenticated review, dispatch claims, reconciliation, and curated incidents. Order/refund fields do not define the booking policy.
- Existing mocked tests do not prove real database constraints or successful live requests.
- Preserve `spec.md` and other unrelated files when integrating branches; their trees omit current documentation.
- Keep browser microphone work behind the shared conversation boundary. Its print-only handoff and Deepgram connection do not satisfy the Twilio P1 requirements.

## Required implementation rules

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

Use server-rendered Jinja2 HTML and small same-origin JavaScript modules. A React build is not required. Implement customer chat, review queue/detail, action history, owner settings/pause, incident curation, and integration status as their issues become ready.

Escape untrusted text, enforce authorization server-side, support keyboard access and small screens, and display readable states. Separate claimed facts from verified facts, review approval from execution success, and configured providers from successful live calls. Handle stale/conflicting submissions with a refresh path. Avoid an uncalibrated maliciousness percentage.

No visual palette or mockup is mandated by the spec. Agree on simple reusable styles before building dashboard screens.

## Issue workflow and verification

1. Read the issue, relevant spec sections, existing code, and repository instructions. Check dependency readiness and current working-tree changes.
2. Work on one issue branch, for example `ssa-003-database-foundation`. Preserve unrelated user/team work; do not reset or overwrite it.
3. Implement the smallest complete change satisfying the issue, including its required tests and documentation. Avoid unrelated refactors.
4. Run checks appropriate to the change. Use unit tests for local logic, real PostgreSQL/pgvector tests for persistence and constraints, and connector/browser tests for workflow behavior.
5. Record actual commands, results, and blockers in the PR. Explain the concrete behavior, link the SSA issue, and identify migration/configuration changes.
6. Merge through the team's established review process and integrate frequently. Do not claim an issue complete while required checks or dependencies remain unresolved.

SSA-001 must establish and document the executable install/run/lint/test commands; do not claim currently nonexistent commands work. Offline CI uses explicit fake providers and no paid requests. Live smoke calls and cloud operations follow the user's authorization and the spec's access requirements. Missing credentials are blockers, not successful tests.

SSA-018 checks actual database and connector effects, including gateway bypass, cross-tenant access, stale approvals, revisions, concurrency, duplicates, unknown outcomes, and response disclosure. Each contributor supplies relevant scenarios; Person 4 coordinates coverage.

P0 is complete only when the spec's release gate is satisfied: required deterministic tests pass on real PostgreSQL, live chat/embedding/Jev evidence exists, text review demonstrates a verified connector result, and Akamai runtime plus managed-database persistence are proven. Keep missing live/cloud evidence explicit.
