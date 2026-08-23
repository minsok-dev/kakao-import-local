-- [변경사유]: I3 — similar decision 감사 컬럼 (decided_by / decided_at)
-- schema_version: 10
-- 기존 DB는 ensure_similar_schema 가드 ALTER 로 적용

ALTER TABLE similar_image_group ADD COLUMN decided_by TEXT;
ALTER TABLE similar_image_group ADD COLUMN decided_at TEXT;

INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', '10');
