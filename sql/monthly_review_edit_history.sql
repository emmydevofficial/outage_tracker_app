-- Chat assistant + editable sections: keep the pre/post text on each saved
-- report and every accepted or rejected edit. Additive only -- does not
-- modify sql/monthly_review_schema.sql, which is already applied.
-- Run once against the outage_tracker PostgreSQL database.

ALTER TABLE mlr_report ADD COLUMN IF NOT EXISTS original_text JSONB;
ALTER TABLE mlr_report ADD COLUMN IF NOT EXISTS final_text JSONB;

CREATE TABLE IF NOT EXISTS mlr_report_edit (
    id           BIGSERIAL PRIMARY KEY,
    report_id    BIGINT REFERENCES mlr_report(id) ON DELETE CASCADE,
    section      TEXT,
    old_text     TEXT,
    new_text     TEXT,
    source       TEXT CHECK (source IN ('manual', 'chat', 'reset')),
    status       TEXT CHECK (status IN ('accepted', 'rejected')),
    reason       TEXT,
    edited_by    TEXT,
    edited_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS mlr_report_edit_report ON mlr_report_edit (report_id);
