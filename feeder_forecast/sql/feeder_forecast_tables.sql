-- Forecast-based outage loss & daily feeder energy: storage for the
-- feeder identity registry, the daily regional workbook uploads, and
-- the per-slot forecast/meter-reading data they produce. Additive
-- only -- reads tcn_sla_compliance but never writes to it, and never
-- touches outages or any other existing table. Run once against the
-- outage_tracker PostgreSQL database.
--
-- Domain timestamps (when a forecast slot starts, when a meter reading
-- was taken) are naive TIMESTAMP, matching the existing outages table's
-- own convention -- the whole app already treats these as Africa/Lagos
-- local time with no stored offset. Audit columns (when a row was
-- created/uploaded/confirmed/resolved, as opposed to what the data
-- itself is about) are TIMESTAMPTZ.
--
-- Creation order matters here (a table can't FK-reference one that
-- doesn't exist yet): feeder -> feeder_alias -> daily_workbook_upload
-- -> daily_workbook_issues -> feeder_upload_staging -> the three
-- per-feeder data tables.

-- ---------------------------------------------------------------------
-- Feeder identity: names change over time, so a name is never a key.
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS feeder (
    feeder_id             BIGSERIAL PRIMARY KEY,
    region                TEXT NOT NULL,
    disco                 TEXT,
    area_control           TEXT,
    station               TEXT NOT NULL,
    station_norm          TEXT NOT NULL,
    transformer           TEXT,
    rating                TEXT,                 -- can be "30/40" etc. -- text, not numeric
    feeder_band           TEXT,
    canonical_name        TEXT NOT NULL,
    status                TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'retired')),
    replaced_by_feeder_id BIGINT REFERENCES feeder(feeder_id),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by            TEXT,
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by            TEXT
);
CREATE INDEX IF NOT EXISTS feeder_station_norm ON feeder (station_norm);
CREATE INDEX IF NOT EXISTS feeder_region ON feeder (region);

CREATE TABLE IF NOT EXISTS feeder_alias (
    alias_id      BIGSERIAL PRIMARY KEY,
    feeder_id     BIGINT NOT NULL REFERENCES feeder(feeder_id),
    source        TEXT NOT NULL CHECK (source IN ('daily_workbook', 'outages', 'sla')),
    station_norm  TEXT NOT NULL,
    name_raw      TEXT NOT NULL,
    name_norm     TEXT NOT NULL,
    valid_from    DATE,
    valid_to      DATE,
    created_by    TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source, station_norm, name_norm, valid_from)
);
CREATE INDEX IF NOT EXISTS feeder_alias_lookup ON feeder_alias (source, station_norm, name_norm);
CREATE INDEX IF NOT EXISTS feeder_alias_feeder ON feeder_alias (feeder_id);

-- ---------------------------------------------------------------------
-- Daily regional workbook uploads.
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS daily_workbook_upload (
    upload_id          BIGSERIAL PRIMARY KEY,
    file_name          TEXT NOT NULL,
    sheet_region       TEXT,                     -- raw Control!L3
    sheet_date         TEXT,                     -- raw Control!L4, kept as text (could be blank/invalid)
    filename_region    TEXT,
    filename_date      DATE,
    resolved_region    TEXT,
    resolved_date      DATE,
    region_source      TEXT CHECK (region_source IN ('sheet', 'filename')),
    date_source        TEXT CHECK (date_source IN ('sheet', 'filename_confirmed')),
    confirmed_by       TEXT,
    confirmed_at       TIMESTAMPTZ,
    confirmation_note  TEXT,
    status             TEXT NOT NULL CHECK (status IN ('saved', 'saved_with_warnings', 'awaiting_confirmation', 'cancelled', 'rejected')),
    feeders_read       INT,
    rows_saved         INT,
    rows_skipped       INT,
    warnings           INT,
    replaced_upload_id BIGINT REFERENCES daily_workbook_upload(upload_id),
    uploaded_by        TEXT,
    uploaded_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS daily_workbook_upload_region_date ON daily_workbook_upload (resolved_region, resolved_date);

CREATE TABLE IF NOT EXISTS daily_workbook_issues (
    issue_id    BIGSERIAL PRIMARY KEY,
    upload_id   BIGINT NOT NULL REFERENCES daily_workbook_upload(upload_id) ON DELETE CASCADE,
    rule_code   TEXT NOT NULL,
    sheet       TEXT,
    cell        TEXT,
    station_raw TEXT,
    feeder_raw  TEXT,
    value_found TEXT,
    expected    TEXT,
    action      TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS daily_workbook_issues_upload ON daily_workbook_issues (upload_id);

-- Unmatched workbook rows wait here until a person links or creates a
-- feeder (section 4's review flow) -- row_data carries everything the
-- parser read for that row, so resolving it later replays the same
-- row-to-tables logic instead of requiring a re-upload.
CREATE TABLE IF NOT EXISTS feeder_upload_staging (
    staging_id         BIGSERIAL PRIMARY KEY,
    upload_id          BIGINT NOT NULL REFERENCES daily_workbook_upload(upload_id) ON DELETE CASCADE,
    station_raw        TEXT,
    feeder_raw         TEXT,
    region             TEXT,
    row_data           JSONB NOT NULL,
    status             TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'resolved', 'discarded')),
    resolved_feeder_id BIGINT REFERENCES feeder(feeder_id),
    resolved_by        TEXT,
    resolved_at        TIMESTAMPTZ,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS feeder_upload_staging_status ON feeder_upload_staging (status);

-- ---------------------------------------------------------------------
-- Per-feeder daily/hourly data parsed from the workbooks.
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS feeder_daily (
    feeder_id            BIGINT NOT NULL REFERENCES feeder(feeder_id),
    reading_date          DATE NOT NULL,
    region                TEXT NOT NULL,
    disco_seen            TEXT,
    feeder_band_seen      TEXT,
    tcn_limit_mw          NUMERIC,
    disco_base_load_mw    NUMERIC,
    disco_peak_load_mw    NUMERIC,
    opening_meter_mwh     NUMERIC,
    closing_meter_mwh     NUMERIC,
    daily_energy_mwh      NUMERIC,
    source_file           TEXT,
    station_raw           TEXT,
    feeder_raw            TEXT,
    upload_id             BIGINT NOT NULL REFERENCES daily_workbook_upload(upload_id),
    uploaded_by           TEXT,
    uploaded_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (feeder_id, reading_date)
);

CREATE TABLE IF NOT EXISTS feeder_hourly_forecast (
    feeder_id    BIGINT NOT NULL REFERENCES feeder(feeder_id),
    slot_date    DATE NOT NULL,
    slot_time    TIME NOT NULL,
    slot_start   TIMESTAMP NOT NULL,
    forecast_mw  NUMERIC,
    source_date  DATE NOT NULL,
    source_label SMALLINT NOT NULL,              -- 1..24, the workbook hour label this slot came from
    upload_id    BIGINT NOT NULL REFERENCES daily_workbook_upload(upload_id),
    PRIMARY KEY (feeder_id, slot_start)
);
CREATE INDEX IF NOT EXISTS feeder_hourly_forecast_slot_date ON feeder_hourly_forecast (slot_date);

CREATE TABLE IF NOT EXISTS feeder_meter_reading (
    feeder_id        BIGINT NOT NULL REFERENCES feeder(feeder_id),
    reading_date     DATE NOT NULL,
    reading_time     TIME NOT NULL,
    reading_at       TIMESTAMP NOT NULL,
    meter_reading_mwh NUMERIC,
    actual_mwh       NUMERIC,                    -- energy for the hour ending at reading_at, NULL for the 00:00 opening reading
    source_date      DATE NOT NULL,
    source_label     SMALLINT NOT NULL,           -- 0 for column L, 1..24 for the hour blocks
    flag             TEXT,                        -- rule code, e.g. R32/R33/R35
    upload_id        BIGINT NOT NULL REFERENCES daily_workbook_upload(upload_id),
    PRIMARY KEY (feeder_id, reading_at)
);
CREATE INDEX IF NOT EXISTS feeder_meter_reading_date ON feeder_meter_reading (reading_date);

-- ---------------------------------------------------------------------
-- Gap-fill approvals for the Reliability KPI (Forecast Loss) page --
-- records exactly what a person approved (date range, filters, the gap
-- list) so a last_load-filled total is always traceable to who signed
-- off on it and when. Nothing is ever filled without a row here.
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS forecast_gap_approval (
    approval_id  BIGSERIAL PRIMARY KEY,
    approved_by  TEXT,
    approved_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    date_from    DATE NOT NULL,
    date_to      DATE NOT NULL,
    filters      JSONB,
    gap_list     JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS forecast_gap_approval_dates ON forecast_gap_approval (date_from, date_to);
