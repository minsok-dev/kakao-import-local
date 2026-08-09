-- [변경사유]: caption-only — 이미 서버에 올린 source SHA 장부 (대역폭 절감)
-- schema_version: 7
-- 파일 재전송 없이 SNS 캡션만 보낼 때 조회

CREATE TABLE IF NOT EXISTS uploaded_sha_ledger (
  source_sha256   TEXT PRIMARY KEY,
  request_idx     INTEGER,
  next            TEXT,
  final_sha_prefix TEXT,
  uploaded_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', '7');
