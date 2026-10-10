# Repository instructions for coding agents

Read [docs/agent-guide.md](docs/agent-guide.md) before implementing changes. Claude and Codex use the same guide.

- `spec.md` is the authoritative product specification. Read the sections and acceptance criteria relevant to the current issue.
- **Until the demo (8:00 am CDT, 2026-10-11), the "Demo scope" section at the top of `spec.md` and its Section 16 catalog override the rules below wherever they conflict.** Build the single demo flow; do not add the deferred production hardening.
- Implement one SSA issue per branch/PR unless the user explicitly requests a broader scope. Preserve issue IDs, dependencies, and requirement traceability.
- Use `backend/` as the shared application package; do not create a competing `app/` implementation. This adapts the suggested layout in spec section 9.4 to the reusable database branch.
- Keep workflow authority in the persisted gateway/review/execution services. Models and input adapters cannot authorize or directly execute bookings.
- Resolve the business from server configuration, never from caller input, and allow at most one booking per action. Immutable action versions and multi-tenant enforcement are deferred.
- Explicitly distinguish fake, live, unavailable, and skipped integrations. Never silently substitute fake results for live failures.
- Never commit secrets, weaken tests to pass, or claim unverified live/cloud results.
- Run checks appropriate to the change and report results and blockers. Do not overwrite unrelated work or merge remote branches wholesale when that would remove the specification.

These instructions do not grant permission for paid provisioning, destructive cleanup, or changes to a live business system. Follow the user's existing authorization and ask only when a required action exceeds it.
