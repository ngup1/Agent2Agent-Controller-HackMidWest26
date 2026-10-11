-- SSA-031: placed orders. Run after 005_ssa014.sql. Do not edit inherited migrations.
-- The salon tables from 004 (slots, bookings) stay in place but are no longer written.
BEGIN;
CREATE TABLE IF NOT EXISTS orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id UUID NOT NULL REFERENCES businesses(id),
    action_id UUID NOT NULL UNIQUE REFERENCES agent_actions(id),
    -- One entry per item ordered: {"code", "name", "price"} as charged.
    items JSONB NOT NULL CHECK (jsonb_typeof(items) = 'array' AND jsonb_array_length(items) > 0),
    total_usd NUMERIC(12,2) NOT NULL CHECK (total_usd >= 0),
    customer_name TEXT NOT NULL,
    callback_number TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS orders_business_created_idx ON orders (business_id, created_at DESC);
COMMIT;
