# SSA-012 Human Review — dependency-independent starter

**Files in this additive overlay**

- `backend/domain/__init__.py`
- `backend/domain/reviews.py`
- `tests/unit/test_reviews.py`

Copy into the root of `Agent2Agent-Controller-HackMidWest26`. No existing database files or Akamai code are replaced. The branch ZIP inspected (`b2729e3c...`) did not have `backend/domain`, so this should be an additive change; nevertheless, compare against your current branch before copying.

## Run

```bash
# From repo root in the project's active virtual environment:
python -m pip install pytest
python -m pytest -q tests/unit/test_reviews.py
# Existing prototype suite (only if its dependencies are installed):
python -m pytest -q
```

No database, cloud provider, or API key is required for the new unit tests.

## What this DOES cover

A pure, frozen-structure contract for review snapshots, allowed booking exceptions, trusted reviewers, exact version/hash/assessment/policy binding, required notes, expiry, soft vs hard rule waivers, same-reviewer identical retries, conflicting decisions, and non-executable follow-up drafts. A contextual Jev concern may be approved with no exception codes while remaining tied to one assessment ID and context digest. Approvals during pause are recorded only; no dispatch occurs.

## What this DOES NOT cover (not implemented or validated)

- Authenticated login/session handling or FastAPI review endpoints (SSA-004).
- Immutable canonical `actions`, state machine, version checks, current confirmation and epoch persistence (SSA-006/011).
- Gateway and Jev/retrieval evidence persistence (SSA-010).
- SQLAlchemy tables, Alembic migrations, real PostgreSQL, unique constraints, tenant-composite FKs, or a transaction (SSA-003 + this issue's integration stage).
- **Actual concurrent reviewer locking or atomicity:** `resolve_review()` is pure; the future service must call it while holding row locks in a DB transaction, then atomically write review, action transition, and audit. Two callers who independently read the same pending record will both receive a proposed approval until the DB serializes them. The tests do not claim T17's concurrency acceptance is satisfied.
- Actual connector booking effects, capacity revalidation or pause/dispatch race (SSA-013).
- Actual persisted expiration or follow-up entry (must be added in the DB-backed service).
- Reviewed-case publication (separate SSA-016).

## Required coordination before service integration

Agree with the SSA-003 owner on `reviews` vs prototype `human_reviews` and action/version foreign keys, and with SSA-004/006/010/011 owners on `TrustedReviewerContext`, action version/hash, assessment/context digest, customer confirmation, session close, and policy-version snapshot. The spec's SQLAlchemy+Alembic structure wins over earlier create-if-missing SQL. Do not implement another competing approval store.

**Spec references:** `spec.md` sections 4.2, 6.2–6.4, 7.5, 8, 9.1–9.2 and tests T02/T15–T18/T30/T34. Note: GitHub issue number **#13** corresponds to **SSA-012**, not SSA-013.
