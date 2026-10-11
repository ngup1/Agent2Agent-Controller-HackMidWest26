-- SSA-014: durable one-at-a-time booking read-back state.
-- Run after 004_demo.sql. Do not edit inherited migrations.
-- The customer must confirm the current proposal before agent_actions is written.
BEGIN;
CREATE TABLE IF NOT EXISTS pending_booking_proposals (
    interaction_id UUID PRIMARY KEY REFERENCES interactions(id) ON DELETE CASCADE,
    business_id UUID NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'confirmed', 'cleared')),
    action_id UUID UNIQUE REFERENCES agent_actions(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    confirmed_at TIMESTAMPTZ,
    CHECK (status <> 'confirmed' OR action_id IS NOT NULL)
);
COMMIT;
