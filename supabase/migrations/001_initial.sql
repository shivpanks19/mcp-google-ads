-- Google Ads MCP: AI memory and reporting backend
-- Run in Supabase SQL Editor or via supabase db push

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ---------------------------------------------------------------------------
-- google_ads_clients
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS google_ads_clients (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id     TEXT NOT NULL UNIQUE,
    descriptive_name TEXT,
    currency_code   TEXT,
    aliases         JSONB NOT NULL DEFAULT '[]'::jsonb,
    notes           TEXT,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_google_ads_clients_descriptive_name
    ON google_ads_clients (lower(descriptive_name));

-- ---------------------------------------------------------------------------
-- memory_entries
-- ---------------------------------------------------------------------------
CREATE TYPE memory_entry_type AS ENUM (
    'context',
    'insight',
    'decision',
    'audit'
);

CREATE TABLE IF NOT EXISTS memory_entries (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id   UUID REFERENCES google_ads_clients (id) ON DELETE SET NULL,
    entry_type  memory_entry_type NOT NULL DEFAULT 'insight',
    title       TEXT,
    content     TEXT NOT NULL,
    tags        JSONB NOT NULL DEFAULT '[]'::jsonb,
    source      TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_memory_entries_client_created
    ON memory_entries (client_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_memory_entries_tags
    ON memory_entries USING GIN (tags);

-- ---------------------------------------------------------------------------
-- report_snapshots
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS report_snapshots (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id     UUID NOT NULL REFERENCES google_ads_clients (id) ON DELETE CASCADE,
    report_type   TEXT NOT NULL,
    period_start  DATE,
    period_end    DATE,
    metrics       JSONB NOT NULL DEFAULT '{}'::jsonb,
    summary       TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_report_snapshots_client_period
    ON report_snapshots (client_id, period_start, period_end);

CREATE INDEX IF NOT EXISTS idx_report_snapshots_client_type
    ON report_snapshots (client_id, report_type, created_at DESC);

-- ---------------------------------------------------------------------------
-- updated_at trigger
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_google_ads_clients_updated_at ON google_ads_clients;
CREATE TRIGGER trg_google_ads_clients_updated_at
    BEFORE UPDATE ON google_ads_clients
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------------------
-- Row Level Security (service role bypasses; deny anon/authenticated by default)
-- ---------------------------------------------------------------------------
ALTER TABLE google_ads_clients ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE report_snapshots ENABLE ROW LEVEL SECURITY;

CREATE POLICY "deny_anon_google_ads_clients"
    ON google_ads_clients FOR ALL TO anon USING (false);

CREATE POLICY "deny_authenticated_google_ads_clients"
    ON google_ads_clients FOR ALL TO authenticated USING (false);

CREATE POLICY "deny_anon_memory_entries"
    ON memory_entries FOR ALL TO anon USING (false);

CREATE POLICY "deny_authenticated_memory_entries"
    ON memory_entries FOR ALL TO authenticated USING (false);

CREATE POLICY "deny_anon_report_snapshots"
    ON report_snapshots FOR ALL TO anon USING (false);

CREATE POLICY "deny_authenticated_report_snapshots"
    ON report_snapshots FOR ALL TO authenticated USING (false);
