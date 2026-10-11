-- SSA-001: minimal booking calendar, using 002/003 vocabulary unchanged.
BEGIN;

ALTER TABLE interactions ADD COLUMN IF NOT EXISTS callback_number TEXT;
ALTER TABLE business_policies
    ADD COLUMN IF NOT EXISTS booking_rules JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(booking_rules) = 'object');

CREATE TABLE IF NOT EXISTS services (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id UUID NOT NULL REFERENCES businesses(id),
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL DEFAULT 30 CHECK (duration_minutes > 0),
    price_usd NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (price_usd >= 0),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (business_id, code)
);

CREATE TABLE IF NOT EXISTS slots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id UUID NOT NULL REFERENCES businesses(id),
    service_id UUID NOT NULL REFERENCES services(id),
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ NOT NULL,
    capacity INTEGER NOT NULL CHECK (capacity > 0),
    closed BOOLEAN NOT NULL DEFAULT FALSE,
    CHECK (end_time > start_time),
    UNIQUE (service_id, start_time)
);
CREATE INDEX IF NOT EXISTS slots_business_time_idx ON slots (business_id, start_time);

CREATE TABLE IF NOT EXISTS bookings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id UUID NOT NULL REFERENCES businesses(id),
    action_id UUID NOT NULL UNIQUE REFERENCES agent_actions(id),
    slot_id UUID NOT NULL REFERENCES slots(id),
    party_size INTEGER NOT NULL CHECK (party_size BETWEEN 1 AND 3),
    customer_name TEXT NOT NULL,
    callback_number TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS bookings_slot_idx ON bookings (slot_id);

COMMIT;
