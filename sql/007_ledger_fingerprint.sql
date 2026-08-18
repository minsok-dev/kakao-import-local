-- [변경사유]: SHA 재사용·캡션 지문·서버 OCR id·거부 캐시 (ingest-dedup-reject-plan v0.2)
-- schema_version: 8
-- 기존 uploaded_sha_ledger 에 컬럼 추가. init_schema 가드 ALTER 와 함께 사용.

ALTER TABLE uploaded_sha_ledger ADD COLUMN media_fingerprint TEXT;
ALTER TABLE uploaded_sha_ledger ADD COLUMN caption_fingerprint TEXT;
ALTER TABLE uploaded_sha_ledger ADD COLUMN ocr_idx INTEGER;
ALTER TABLE uploaded_sha_ledger ADD COLUMN result_type TEXT;
ALTER TABLE uploaded_sha_ledger ADD COLUMN rejected INTEGER NOT NULL DEFAULT 0;
ALTER TABLE uploaded_sha_ledger ADD COLUMN rejected_at TEXT;
ALTER TABLE uploaded_sha_ledger ADD COLUMN asset_ids TEXT;

INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', '8');
