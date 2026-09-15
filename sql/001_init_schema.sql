-- [변경사유]: Phase 1 스키마 — batch/chat/message/photo/group/assignment/exact/review (멱등 UNIQUE)
-- version: 2
-- 기존 001 stub 대체. init 시 본 파일만 적용(스키마 재작성).

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', '2');

-- 처리 배치
CREATE TABLE IF NOT EXISTS import_batch (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  root_rel      TEXT NOT NULL,
  started_at    TEXT NOT NULL,
  finished_at   TEXT,
  status        TEXT NOT NULL DEFAULT 'running',
  summary_json  TEXT
);

-- 채팅 소스 파일
CREATE TABLE IF NOT EXISTS chat_source (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  rel_path        TEXT NOT NULL UNIQUE,
  room_title      TEXT,
  file_size       INTEGER,
  mtime_ns        INTEGER,
  content_sha256  TEXT NOT NULL,
  encoding        TEXT,
  last_batch_id   INTEGER REFERENCES import_batch(id),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 파싱된 메시지
CREATE TABLE IF NOT EXISTS parsed_message (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id         INTEGER NOT NULL REFERENCES chat_source(id) ON DELETE CASCADE,
  seq             INTEGER NOT NULL,
  msg_kind        TEXT NOT NULL, -- text | photo | photo_multi | video | emoji | file | system | other
  sender          TEXT,
  abs_time        TEXT,          -- ISO-8601
  body_raw        TEXT NOT NULL,
  body_norm       TEXT,
  photo_count     INTEGER,       -- 사진 N장 슬롯 수 (사진=1)
  line_no         INTEGER,
  UNIQUE (chat_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_msg_chat_time ON parsed_message(chat_id, abs_time);
CREATE INDEX IF NOT EXISTS idx_msg_kind ON parsed_message(msg_kind);

-- 사진 파일 (공용 photos/)
CREATE TABLE IF NOT EXISTS photo_file (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  rel_path        TEXT NOT NULL UNIQUE,
  file_name       TEXT NOT NULL,
  ext             TEXT,
  byte_size       INTEGER,
  mtime_ns        INTEGER,
  name_time       TEXT,          -- 파일명 파싱 시각 ISO
  name_parse_ok   INTEGER NOT NULL DEFAULT 0,
  sha256          TEXT,
  last_batch_id   INTEGER REFERENCES import_batch(id),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_photo_sha ON photo_file(sha256);
CREATE INDEX IF NOT EXISTS idx_photo_name_time ON photo_file(name_time);

-- 이미지 그룹 (연속 사진)
CREATE TABLE IF NOT EXISTS image_group (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id        INTEGER NOT NULL REFERENCES import_batch(id) ON DELETE CASCADE,
  chat_id         INTEGER NOT NULL REFERENCES chat_source(id) ON DELETE CASCADE,
  group_key       TEXT NOT NULL,
  confidence      TEXT,          -- high | medium | ambiguous
  review_required INTEGER NOT NULL DEFAULT 0,
  match_reason    TEXT,
  UNIQUE (batch_id, group_key)
);

-- 사진↔메시지 배정
CREATE TABLE IF NOT EXISTS photo_message_assignment (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id        INTEGER NOT NULL REFERENCES import_batch(id) ON DELETE CASCADE,
  group_id        INTEGER REFERENCES image_group(id) ON DELETE CASCADE,
  photo_id        INTEGER NOT NULL REFERENCES photo_file(id) ON DELETE CASCADE,
  message_id      INTEGER REFERENCES parsed_message(id) ON DELETE SET NULL,
  slot_index      INTEGER NOT NULL DEFAULT 0,
  confidence      TEXT NOT NULL,
  match_reason    TEXT,
  review_required INTEGER NOT NULL DEFAULT 0,
  UNIQUE (batch_id, photo_id)
);
CREATE INDEX IF NOT EXISTS idx_assign_msg ON photo_message_assignment(message_id);
-- [변경사유]: 캡션 조회 WHERE photo_id / 그룹 first_seq 조인 — 풀스캔 방지
CREATE INDEX IF NOT EXISTS idx_assign_photo ON photo_message_assignment(photo_id);
CREATE INDEX IF NOT EXISTS idx_assign_group ON photo_message_assignment(group_id, message_id);

-- 그룹 공통 설명 텍스트
CREATE TABLE IF NOT EXISTS group_text (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  group_id        INTEGER NOT NULL REFERENCES image_group(id) ON DELETE CASCADE,
  message_id      INTEGER NOT NULL REFERENCES parsed_message(id) ON DELETE CASCADE,
  seq_in_group    INTEGER NOT NULL,
  UNIQUE (group_id, message_id)
);

-- SHA exact 그룹 (이력 포함)
CREATE TABLE IF NOT EXISTS exact_sha_group (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  sha256          TEXT NOT NULL UNIQUE,
  representative_photo_id INTEGER REFERENCES photo_file(id),
  member_count    INTEGER NOT NULL DEFAULT 0,
  updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS exact_sha_member (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  group_id        INTEGER NOT NULL REFERENCES exact_sha_group(id) ON DELETE CASCADE,
  photo_id        INTEGER NOT NULL REFERENCES photo_file(id) ON DELETE CASCADE,
  is_representative INTEGER NOT NULL DEFAULT 0,
  excluded_from_upload INTEGER NOT NULL DEFAULT 0,
  UNIQUE (photo_id)
);
-- [변경사유]: exact SHA 확장 시 group_id 조인
CREATE INDEX IF NOT EXISTS idx_exact_member_group ON exact_sha_member(group_id);

-- 리뷰 항목
CREATE TABLE IF NOT EXISTS review_item (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id        INTEGER NOT NULL REFERENCES import_batch(id) ON DELETE CASCADE,
  kind            TEXT NOT NULL,
  ref_type        TEXT,
  ref_id          INTEGER,
  reason          TEXT NOT NULL,
  payload_json    TEXT,
  status          TEXT NOT NULL DEFAULT 'pending'
);

-- 파서/매처 오류·미파싱
CREATE TABLE IF NOT EXISTS parse_error (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id        INTEGER NOT NULL REFERENCES import_batch(id) ON DELETE CASCADE,
  source_rel      TEXT,
  line_no         INTEGER,
  raw_excerpt     TEXT,
  error_code      TEXT NOT NULL,
  detail          TEXT,
  UNIQUE (batch_id, source_rel, line_no, error_code)
);
