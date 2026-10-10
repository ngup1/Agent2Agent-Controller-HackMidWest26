-- AI Agent Governance Gateway: proposed Hack Midwest extension.
-- Apply AFTER the Akamai starter's 001_init.sql.
-- PostgreSQL 17 + pgvector; uses workshop embedding vector(1024).
-- Safe to re-run CREATE statements; it does not modify existing starter tables.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS businesses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    industry TEXT NOT NULL DEFAULT 'other',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS business_policies (
    business_id UUID PRIMARY KEY REFERENCES businesses(id) ON DELETE CASCADE,
    automatic_ordering BOOLEAN NOT NULL DEFAULT TRUE,
    maximum_order_amount_usd NUMERIC(12,2) NOT NULL DEFAULT 100.00
        CHECK (maximum_order_amount_usd >= 0),
    refund_approval_required BOOLEAN NOT NULL DEFAULT TRUE,
    automation_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- A generic interaction can be a telephone call, an API message, or an A2A exchange.
CREATE TABLE IF NOT EXISTS interactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id UUID NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    channel TEXT NOT NULL CHECK (channel IN ('phone', 'api', 'a2a', 'web')),
    external_reference TEXT,              -- e.g., Twilio Call SID (untrusted metadata)
    caller_identity_status TEXT NOT NULL DEFAULT 'unverified'
        CHECK (caller_identity_status IN ('unverified', 'verified', 'rejected')),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'completed', 'escalated', 'blocked', 'failed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS interactions_business_time_idx
    ON interactions (business_id, created_at DESC);

-- Transcript is stored as text segments to support real-time transcripts and speaker labels.
CREATE TABLE IF NOT EXISTS transcript_segments (
    id BIGSERIAL PRIMARY KEY,
    interaction_id UUID NOT NULL REFERENCES interactions(id) ON DELETE CASCADE,
    sequence_number INT NOT NULL CHECK (sequence_number >= 0),
    speaker TEXT NOT NULL CHECK (speaker IN ('caller', 'business_agent', 'human', 'unknown')),
    content TEXT NOT NULL,
    start_ms INT CHECK (start_ms >= 0),
    end_ms INT CHECK (end_ms IS NULL OR end_ms >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (interaction_id, sequence_number)
);

-- One vector per text segment for the MVP. Do not embed full raw audio here.
CREATE TABLE IF NOT EXISTS transcript_embeddings (
    transcript_segment_id BIGINT PRIMARY KEY REFERENCES transcript_segments(id) ON DELETE CASCADE,
    embedding_model TEXT NOT NULL DEFAULT 'hackathon-embedding',
    embedding vector(1024) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS transcript_embeddings_hnsw_idx
    ON transcript_embeddings USING hnsw (embedding vector_cosine_ops);

-- Proposed business actions are not executed until deterministic policy checks pass.
CREATE TABLE IF NOT EXISTS agent_actions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    interaction_id UUID NOT NULL REFERENCES interactions(id) ON DELETE CASCADE,
    business_id UUID NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    action_type TEXT NOT NULL CHECK (action_type IN ('place_order', 'refund', 'book_appointment', 'request_quote', 'other')),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    idempotency_key TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'proposed'
        CHECK (state IN ('proposed', 'allowed', 'pending_review', 'rejected', 'executed', 'failed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (business_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS security_decisions (
    id BIGSERIAL PRIMARY KEY,
    interaction_id UUID NOT NULL REFERENCES interactions(id) ON DELETE CASCADE,
    action_id UUID REFERENCES agent_actions(id) ON DELETE SET NULL,
    decision TEXT NOT NULL CHECK (decision IN ('allow', 'clarify', 'escalate', 'block')),
    risk_level TEXT NOT NULL CHECK (risk_level IN ('low', 'medium', 'high', 'unknown')),
    reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
    policy_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS security_decisions_interaction_idx
    ON security_decisions (interaction_id, created_at DESC);

CREATE TABLE IF NOT EXISTS human_reviews (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    action_id UUID NOT NULL UNIQUE REFERENCES agent_actions(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'denied', 'expired')),
    reviewer_id TEXT,
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS audit_events (
    id BIGSERIAL PRIMARY KEY,
    business_id UUID NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    interaction_id UUID REFERENCES interactions(id) ON DELETE SET NULL,
    event_type TEXT NOT NULL,
    actor_type TEXT NOT NULL DEFAULT 'system',
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS audit_events_business_time_idx
    ON audit_events (business_id, created_at DESC);

-- EXAMPLE data (run manually, not during every migration):
-- INSERT INTO businesses (name, industry) VALUES ('Demo Restaurant', 'restaurant') RETURNING id;
-- INSERT INTO business_policies (business_id) VALUES ('<id-from-above>');
-- For production: enforce tenant access with server-side authorization and/or PostgreSQL RLS,
-- require authenticated owner approvals, minimize PII, set retention policies, and validate
-- trusted telephony webhooks.  This schema is a hackathon prototype, not production security.
