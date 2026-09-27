-- SLA Outage Reports: storage for generated report runs, allowance
-- overrides and human notes. Additive only -- reads outages,
-- tcn_sla_compliance, tariff_rates, tariff_settings but never writes to
-- them, and never touches any existing mlr_*/other table.
-- Run once against the outage_tracker PostgreSQL database.

-- One row per document (the management summary, scope='ALL', or one
-- region's report, scope=<region name>). A single "Generate drafts"
-- action for a period creates one row per document produced, sharing the
-- same period_type/period_start/period_end. "Issued" rows are what a
-- later period's comparison-with-previous-period figures are read from.
CREATE TABLE IF NOT EXISTS rel_report_run (
    id             BIGSERIAL PRIMARY KEY,
    period_type    TEXT NOT NULL CHECK (period_type IN ('week', 'month')),
    period_start   DATE NOT NULL,
    period_end     DATE NOT NULL,
    period_label   TEXT NOT NULL,
    scope          TEXT NOT NULL,              -- 'ALL' (management summary) or a region name
    status         TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'issued')),
    facts          JSONB,                       -- every figure the report was built from
    ai_text        JSONB,                       -- section -> paragraph text
    notes          JSONB,                       -- number-check fallback notes
    model          TEXT,                        -- 'rules' or the model id used
    prompt_version TEXT,
    docx           BYTEA,                        -- the rendered Word file
    created_by     TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    issued_by      TEXT,
    issued_at      TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS rel_report_run_period ON rel_report_run (period_type, period_start, period_end);
CREATE INDEX IF NOT EXISTS rel_report_run_status ON rel_report_run (status);

-- Manual correction to a feeder's allowed outage hours for a date range
-- (e.g. agreed load shedding under force majeure) -- printed in any report
-- that uses it.
CREATE TABLE IF NOT EXISTS rel_allowance_override (
    id            BIGSERIAL PRIMARY KEY,
    station       TEXT NOT NULL,
    feeder        TEXT NOT NULL,
    date_from     DATE NOT NULL,
    date_to       DATE NOT NULL,
    allowed_hours_per_day  NUMERIC,             -- NULL if exclude_flag is true
    exclude_flag  BOOLEAN NOT NULL DEFAULT false,
    reason        TEXT NOT NULL,
    entered_by    TEXT,
    entered_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Human-supplied facts the AI writer must include verbatim-ish (e.g. the
-- Shiroro/Birnin-Kebbi misattribution-correction note in the real Week 3
-- report). region NULL/'ALL' applies to the management summary.
CREATE TABLE IF NOT EXISTS rel_report_note (
    id            BIGSERIAL PRIMARY KEY,
    period_start  DATE NOT NULL,
    period_end    DATE NOT NULL,
    region        TEXT,                         -- NULL or 'ALL' for network-wide
    kind          TEXT NOT NULL CHECK (kind IN ('intervention', 'provisional', 'general')),
    text          TEXT NOT NULL,
    entered_by    TEXT,
    entered_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS rel_report_note_period ON rel_report_note (period_start, period_end);
