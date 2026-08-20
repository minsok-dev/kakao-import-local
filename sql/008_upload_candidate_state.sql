-- [변경사유]: 증분 업로드/스케줄러 — 후보 상태/멤버/실행 락 저장
-- schema_version: 9

CREATE TABLE IF NOT EXISTS upload_candidate (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  candidate_key TEXT NOT NULL UNIQUE,
  media_fingerprint TEXT NOT NULL,
  caption_fingerprint TEXT,
  evaluation_fingerprint TEXT,
  state TEXT NOT NULL DEFAULT 'new',
  state_reason TEXT,
  caption_text_cached TEXT,
  poster_status TEXT,
  poster_confidence REAL,
  poster_decision_source TEXT,
  poster_classifier_version TEXT,
  similar_group_id INTEGER,
  similar_decision TEXT,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  next_retry_at TEXT,
  last_error_code TEXT,
  last_error TEXT,
  server_receipt_id TEXT,
  server_ocr_id INTEGER,
  lease_owner TEXT,
  lease_expires_at TEXT,
  needs_rebuild INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  uploaded_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_upload_candidate_state
  ON upload_candidate(state, next_retry_at, updated_at);

CREATE INDEX IF NOT EXISTS idx_upload_candidate_media_fp
  ON upload_candidate(media_fingerprint);

CREATE INDEX IF NOT EXISTS idx_upload_candidate_eval_fp
  ON upload_candidate(evaluation_fingerprint);

CREATE INDEX IF NOT EXISTS idx_upload_candidate_similar_group
  ON upload_candidate(similar_group_id);

CREATE TABLE IF NOT EXISTS upload_candidate_member (
  candidate_id INTEGER NOT NULL,
  photo_id INTEGER NOT NULL,
  sha256 TEXT,
  member_role TEXT NOT NULL DEFAULT 'main',
  sort_order INTEGER NOT NULL DEFAULT 0,
  rel_path TEXT,
  PRIMARY KEY (candidate_id, photo_id),
  FOREIGN KEY (candidate_id) REFERENCES upload_candidate(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_upload_candidate_member_role
  ON upload_candidate_member(candidate_id, member_role, sort_order);

CREATE TABLE IF NOT EXISTS upload_run_lock (
  lock_name TEXT PRIMARY KEY,
  owner TEXT,
  acquired_at TEXT NOT NULL DEFAULT (datetime('now')),
  lease_expires_at TEXT
);

INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', '9');
