-- [변경사유]: 포스터 vs 잡사진 판정 — exact_sha_member 와 분리. 원본 파일 불변
-- schema_version: 8

CREATE TABLE IF NOT EXISTS poster_embedding (
  sha256      TEXT NOT NULL,
  clip_model  TEXT NOT NULL,
  dim         INTEGER NOT NULL,
  vector      BLOB NOT NULL,
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (sha256, clip_model)
);

CREATE TABLE IF NOT EXISTS poster_classify (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  room_id               TEXT NOT NULL,
  photo_id              INTEGER REFERENCES photo_file(id) ON DELETE SET NULL,
  sha256                TEXT NOT NULL,
  rel_path              TEXT NOT NULL,
  file_name             TEXT NOT NULL,
  model_version         TEXT,
  poster_score          REAL,
  status                TEXT NOT NULL,
  source                TEXT NOT NULL,
  excluded_from_upload  INTEGER NOT NULL DEFAULT 0,
  classified_at         TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (room_id, sha256)
);
CREATE INDEX IF NOT EXISTS idx_poster_classify_sha ON poster_classify(sha256);
CREATE INDEX IF NOT EXISTS idx_poster_classify_excl ON poster_classify(excluded_from_upload);

CREATE TABLE IF NOT EXISTS poster_model_meta (
  version            TEXT PRIMARY KEY,
  clip_model         TEXT NOT NULL,
  exclude_threshold  REAL NOT NULL,
  poster_threshold   REAL NOT NULL,
  metrics_json       TEXT,
  created_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', '8');
