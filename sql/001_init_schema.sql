-- [변경사유]: Phase 0 SQLite 스키마 — 로컬 collector 인덱스 (원본 파일 미저장)
-- version: 1

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', '1');

-- 채팅방/소스 단위
CREATE TABLE IF NOT EXISTS source_room (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  room_key     TEXT NOT NULL UNIQUE,
  display_name TEXT,
  root_path    TEXT NOT NULL,
  scanned_at   TEXT,
  created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 메시지
CREATE TABLE IF NOT EXISTS local_message (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  room_id      INTEGER NOT NULL REFERENCES source_room(id) ON DELETE CASCADE,
  external_id  TEXT,
  sent_at      TEXT,
  sender       TEXT,
  body_text    TEXT,
  raw_ref      TEXT,
  created_at   TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (room_id, external_id)
);

CREATE INDEX IF NOT EXISTS idx_local_message_room ON local_message(room_id);
CREATE INDEX IF NOT EXISTS idx_local_message_sent ON local_message(sent_at);

-- 미디어 (이미지 등) — SHA-256 exact용
CREATE TABLE IF NOT EXISTS local_media (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  room_id       INTEGER NOT NULL REFERENCES source_room(id) ON DELETE CASCADE,
  message_id    INTEGER REFERENCES local_message(id) ON DELETE SET NULL,
  rel_path      TEXT NOT NULL,
  abs_path      TEXT NOT NULL,
  sha256        TEXT,
  byte_size     INTEGER,
  mime_hint     TEXT,
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (room_id, rel_path)
);

CREATE INDEX IF NOT EXISTS idx_local_media_sha ON local_media(sha256);
CREATE INDEX IF NOT EXISTS idx_local_media_room ON local_media(room_id);

-- 매칭 결과 (Phase1: exact SHA)
CREATE TABLE IF NOT EXISTS match_result (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  media_id        INTEGER NOT NULL REFERENCES local_media(id) ON DELETE CASCADE,
  match_kind      TEXT NOT NULL, -- exact | similar | none
  peer_sha256     TEXT,
  peer_ref        TEXT,         -- 서버/카탈로그 참조 (Phase3+)
  confidence      REAL,
  decision_hint   TEXT,         -- exact_same_text | exact_diff_text | review | unmatched
  notes           TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (media_id)
);

CREATE INDEX IF NOT EXISTS idx_match_kind ON match_result(match_kind);

-- 리뷰 큐 (Phase2+ stub)
CREATE TABLE IF NOT EXISTS review_queue (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  media_id     INTEGER REFERENCES local_media(id) ON DELETE CASCADE,
  message_id   INTEGER REFERENCES local_message(id) ON DELETE CASCADE,
  reason       TEXT NOT NULL,
  status       TEXT NOT NULL DEFAULT 'pending', -- pending | done | skipped
  payload_json TEXT,
  created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_review_status ON review_queue(status);
