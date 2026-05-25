-- Narrative analysis reports (Markdown / plain text) keyed by client, type, and created_at.
-- Run in Supabase SQL Editor after 001_initial.sql (or supabase db push).

CREATE TABLE IF NOT EXISTS analysis_text_snapshots (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id      UUID NOT NULL REFERENCES google_ads_clients (id) ON DELETE CASCADE,
    analysis_type  TEXT NOT NULL,
    title          TEXT,
    body           TEXT NOT NULL,
    metadata       JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_analysis_text_snapshots_client_created
    ON analysis_text_snapshots (client_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_analysis_text_snapshots_client_type_created
    ON analysis_text_snapshots (client_id, analysis_type, created_at DESC);

ALTER TABLE analysis_text_snapshots ENABLE ROW LEVEL SECURITY;

CREATE POLICY "deny_anon_analysis_text_snapshots"
    ON analysis_text_snapshots FOR ALL TO anon USING (false);

CREATE POLICY "deny_authenticated_analysis_text_snapshots"
    ON analysis_text_snapshots FOR ALL TO authenticated USING (false);
