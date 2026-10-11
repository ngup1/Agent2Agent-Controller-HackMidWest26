-- AI Agent Governance Gateway — protected-agent security additions.
-- Apply AFTER backend/database/migrations/002_governance.sql.
-- Intended for PostgreSQL. Untrusted callers must NEVER write to these tables directly.
-- This is a schema extension; tool authorization is enforced by trusted backend code.

BEGIN;

-- A caller can claim to be another agent without proving it.
-- Only the trusted gateway may populate authenticated_subject_id after verification.
ALTER TABLE interactions
    ADD COLUMN IF NOT EXISTS caller_type_claimed TEXT NOT NULL DEFAULT 'unknown'
        CHECK (caller_type_claimed IN ('human', 'external_agent', 'unknown'));
ALTER TABLE interactions
    ADD COLUMN IF NOT EXISTS authenticated_subject_id TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS interactions_business_id_id_key
    ON interactions (business_id, id);

-- Businesses may own multiple independently configured AI agents.
CREATE TABLE IF NOT EXISTS business_agents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id UUID NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    purpose TEXT NOT NULL DEFAULT 'customer_service',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (business_id, name),
    UNIQUE (business_id, id)
);

-- Per-agent, per-tool policy. Everything unspecified is DENIED by backend default.
-- For example: agent=front_desk, resource=orders, action=create, allowed=true.
CREATE TABLE IF NOT EXISTS agent_capability_policies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id UUID NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    business_agent_id UUID NOT NULL,
    resource TEXT NOT NULL,
    action TEXT NOT NULL,
    allowed BOOLEAN NOT NULL DEFAULT FALSE,
    human_approval_required BOOLEAN NOT NULL DEFAULT TRUE,
    max_amount_usd NUMERIC(12,2) CHECK (max_amount_usd IS NULL OR max_amount_usd >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (business_agent_id, resource, action),
    FOREIGN KEY (business_id, business_agent_id)
        REFERENCES business_agents(business_id, id) ON DELETE CASCADE
);

-- Record proposed tool calls BEFORE executing them. Includes attempts to access
-- restricted resources, even when nothing was executed.
CREATE TABLE IF NOT EXISTS tool_execution_attempts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id UUID NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    interaction_id UUID NOT NULL,
    business_agent_id UUID NOT NULL,
    resource TEXT NOT NULL,
    action TEXT NOT NULL,
    arguments_redacted JSONB NOT NULL DEFAULT '{}'::jsonb,
    idempotency_key TEXT,
    decision TEXT NOT NULL DEFAULT 'pending'
        CHECK (decision IN ('pending', 'allow', 'review', 'block', 'clarify')),
    execution_status TEXT NOT NULL DEFAULT 'not_started'
        CHECK (execution_status IN ('not_started', 'succeeded', 'failed', 'uncertain')),
    decision_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at TIMESTAMPTZ,
    executed_at TIMESTAMPTZ,
    FOREIGN KEY (business_id, interaction_id)
        REFERENCES interactions(business_id, id) ON DELETE CASCADE,
    FOREIGN KEY (business_id, business_agent_id)
        REFERENCES business_agents(business_id, id) ON DELETE CASCADE,
    CHECK (execution_status = 'not_started' OR decision = 'allow')
);
CREATE INDEX IF NOT EXISTS tool_attempts_business_time_idx
    ON tool_execution_attempts (business_id, created_at DESC);
CREATE INDEX IF NOT EXISTS tool_attempts_interaction_idx
    ON tool_execution_attempts (interaction_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS tool_attempts_idempotency_idx
    ON tool_execution_attempts (business_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

-- Store Jev/classifier output as advisory evidence alongside final policy decisions.
-- Do not treat a risk score as authorization to execute tools.
ALTER TABLE security_decisions
    ADD COLUMN IF NOT EXISTS tool_attempt_id UUID
        REFERENCES tool_execution_attempts(id) ON DELETE SET NULL;
ALTER TABLE security_decisions
    ADD COLUMN IF NOT EXISTS risk_score NUMERIC(5,4)
        CHECK (risk_score IS NULL OR (risk_score >= 0 AND risk_score <= 1));
ALTER TABLE security_decisions
    ADD COLUMN IF NOT EXISTS risk_engine TEXT;
ALTER TABLE security_decisions
    ADD COLUMN IF NOT EXISTS risk_categories TEXT[] NOT NULL DEFAULT '{}';

-- Business-wide protections. The existing automatic_ordering/refund rules remain.
ALTER TABLE business_policies
    ADD COLUMN IF NOT EXISTS sensitive_data_approval_required BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE business_policies
    ADD COLUMN IF NOT EXISTS requests_per_minute_limit INT NOT NULL DEFAULT 30
        CHECK (requests_per_minute_limit > 0);

-- A deletion/retention worker must act on this timestamp; the column alone
-- DOES NOT automatically delete transcripts or their embeddings.
ALTER TABLE transcript_segments
    ADD COLUMN IF NOT EXISTS retention_expires_at TIMESTAMPTZ;

COMMIT;