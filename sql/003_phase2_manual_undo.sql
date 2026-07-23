-- [변경사유]: Phase2 보완 — 수동 결정·되돌리기 메타 (decision_source / manual_note)
-- schema_version: 4
-- 선행: 002_phase2_text_merge.sql

INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', '4');

-- SQLite: 컬럼 추가 (이미 있으면 시 init에서 무시 가능하도록 앱에서 가드)
ALTER TABLE text_merge ADD COLUMN decision_source TEXT NOT NULL DEFAULT 'auto';
-- auto | manual

ALTER TABLE text_merge ADD COLUMN manual_note TEXT;
