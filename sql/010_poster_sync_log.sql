-- [변경사유]: I6/B2 — human 라벨 → dataset sync 이력 (sha·status·결과)
-- schema_version: 11

CREATE TABLE IF NOT EXISTS poster_sync_log (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  sha256      TEXT NOT NULL,
  room_id     TEXT NOT NULL,
  status      TEXT NOT NULL,
  dest_name   TEXT,
  result      TEXT NOT NULL,
  synced_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_poster_sync_log_sha ON poster_sync_log(sha256);
CREATE INDEX IF NOT EXISTS idx_poster_sync_log_at ON poster_sync_log(synced_at);

INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', '11');
