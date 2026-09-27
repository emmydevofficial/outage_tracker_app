-- Monthly load returns: storage for the ten TCC workbooks and the generated reviews.
-- Run once against the outage_tracker PostgreSQL database.

CREATE TABLE IF NOT EXISTS mlr_upload_batch (
    id            BIGSERIAL PRIMARY KEY,
    period        DATE        NOT NULL,              -- first day of the reporting month
    region        TEXT        NOT NULL,
    file_name     TEXT        NOT NULL,
    file_sha256   TEXT        NOT NULL,
    uploaded_by   TEXT,
    uploaded_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    row_count     INT,
    issue_count   INT,
    UNIQUE (period, region)                          -- re-uploading a region replaces it
);

CREATE TABLE IF NOT EXISTS mlr_reading (
    id                 BIGSERIAL PRIMARY KEY,
    batch_id           BIGINT NOT NULL REFERENCES mlr_upload_batch(id) ON DELETE CASCADE,
    period             DATE   NOT NULL,
    region             TEXT   NOT NULL,
    kind               TEXT   NOT NULL CHECK (kind IN ('transformer','feeder','line')),
    equipment_key      TEXT   NOT NULL,             -- region|kind|substation|name, used to match months
    sheet_row          INT,
    substation         TEXT,
    name               TEXT   NOT NULL,
    nomenclature       TEXT,
    rating_mva         NUMERIC,
    max_mw             NUMERIC, max_amps NUMERIC, max_kv NUMERIC, max_time TEXT, max_date DATE,
    temp_primary_max   NUMERIC, temp_secondary_max NUMERIC,
    min_mw             NUMERIC, min_amps NUMERIC, min_kv NUMERIC, min_time TEXT, min_date DATE,
    energy_reading     NUMERIC,                     -- closing register at 24:00 on the last day
    status_text        TEXT,
    remarks            TEXT,
    raw                JSONB                        -- the cells as they were typed
);
CREATE UNIQUE INDEX IF NOT EXISTS mlr_reading_unique ON mlr_reading (period, equipment_key, sheet_row);
CREATE INDEX IF NOT EXISTS mlr_reading_key ON mlr_reading (equipment_key, period);

CREATE TABLE IF NOT EXISTS mlr_issue (
    id        BIGSERIAL PRIMARY KEY,
    batch_id  BIGINT NOT NULL REFERENCES mlr_upload_batch(id) ON DELETE CASCADE,
    kind      TEXT, sheet_row INT, field TEXT, problem TEXT, raw TEXT
);

-- One row per meter that needs a unit or multiplier different from 1 (MWh assumed otherwise).
CREATE TABLE IF NOT EXISTS mlr_meter (
    equipment_key  TEXT PRIMARY KEY,
    unit           TEXT NOT NULL DEFAULT 'MWh' CHECK (unit IN ('MWh','kWh')),
    multiplier     NUMERIC NOT NULL DEFAULT 1,
    tariff_band    TEXT,                           -- A to E for DisCo feeders
    note           TEXT
);

-- Tariffs change: keep every rate with its effective dates, never overwrite.
CREATE TABLE IF NOT EXISTS mlr_tariff (
    id              BIGSERIAL PRIMARY KEY,
    band            TEXT    NOT NULL,
    naira_per_kwh   NUMERIC NOT NULL,
    effective_from  DATE    NOT NULL,
    effective_to    DATE,
    source          TEXT    NOT NULL                -- order or circular the rate comes from
);

CREATE TABLE IF NOT EXISTS mlr_report (
    id              BIGSERIAL PRIMARY KEY,
    period          DATE        NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by      TEXT,
    narrative_by    TEXT,                           -- 'rules' or the Claude model id
    prompt_version  TEXT,
    facts           JSONB       NOT NULL,           -- exactly what the words were written from
    html            TEXT        NOT NULL,
    notes           JSONB
);

-- Energy used per meter: this month's closing reading minus last month's.
CREATE OR REPLACE VIEW mlr_energy AS
SELECT r.period, r.region, r.kind, r.equipment_key, r.substation, r.name, r.max_mw,
       r.energy_reading                                             AS closing,
       p.energy_reading                                             AS opening,
       (r.energy_reading - p.energy_reading)
         * COALESCE(m.multiplier, 1)
         / CASE WHEN m.unit = 'kWh' THEN 1000 ELSE 1 END            AS mwh
FROM mlr_reading r
LEFT JOIN mlr_reading p
       ON p.equipment_key = r.equipment_key
      AND p.period = (r.period - INTERVAL '1 month')::date
LEFT JOIN mlr_meter m ON m.equipment_key = r.equipment_key;
