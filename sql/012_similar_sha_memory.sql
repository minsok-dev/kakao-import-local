-- [변경사유]: similar 사람 결정을 그룹이 아니라 SHA 에 보존
-- 멤버가 한 장 붙거나 빠져도 기존 설정이 보류로 되돌아가지 않게 한다.
-- 기존 DB 는 ensure_similar_schema / _apply_similar_sha_memory 가드가 적용한다.
-- schema_version: 11

CREATE TABLE IF NOT EXISTS similar_sha_memory (
  sha_key TEXT PRIMARY KEY,
  canonical_key TEXT NOT NULL,
  is_solo INTEGER NOT NULL DEFAULT 0,
  decided_by TEXT,
  decided_at TEXT
);

INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', '11');
