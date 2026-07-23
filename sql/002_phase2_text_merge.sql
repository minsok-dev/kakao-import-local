-- [변경사유]: Phase 2 — exact SHA 그룹 텍스트 merge / 충돌 / 되돌리기용 이력
-- schema_version: 3

INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', '3');

CREATE TABLE IF NOT EXISTS text_merge (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  sha256            TEXT NOT NULL,
  mode              TEXT NOT NULL, -- safe | balanced | auto
  decision          TEXT NOT NULL, -- collapse | merged | review | skipped
  review_required   INTEGER NOT NULL DEFAULT 0,
  merged_text       TEXT,
  merged_norm       TEXT,
  before_json       TEXT NOT NULL, -- 병합 전 소스 스냅샷 (되돌리기)
  after_json        TEXT,          -- 병합 후 스냅샷
  status            TEXT NOT NULL DEFAULT 'active', -- active | superseded
  created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_text_merge_sha ON text_merge(sha256, status);

CREATE TABLE IF NOT EXISTS text_merge_source (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  merge_id      INTEGER NOT NULL REFERENCES text_merge(id) ON DELETE CASCADE,
  photo_id      INTEGER NOT NULL REFERENCES photo_file(id) ON DELETE CASCADE,
  message_id    INTEGER REFERENCES parsed_message(id) ON DELETE SET NULL,
  body_raw      TEXT NOT NULL,
  body_norm     TEXT,
  abs_time      TEXT,
  seq_in_source INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS text_merge_conflict (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  merge_id      INTEGER NOT NULL REFERENCES text_merge(id) ON DELETE CASCADE,
  field_name    TEXT NOT NULL,
  values_json   TEXT NOT NULL
);
