-- [변경사유]: Phase 4.0 — 로컬 similar 그룹 탐지·decision 저장 (upload 동작 변경 없음)
-- decision = 콘텐츠 관계 판단, upload policy = 큐 적용 규칙(4.2+에서 사용)
-- 자동 병합·자동 삭제 없음

CREATE TABLE IF NOT EXISTS photo_signature (
  photo_id        INTEGER PRIMARY KEY REFERENCES photo_file(id) ON DELETE CASCADE,
  algo_version    TEXT NOT NULL,
  dhash_hex       TEXT NOT NULL,
  phash_hex       TEXT NOT NULL,
  source_sha256   TEXT,
  computed_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS similar_image_group (
  id                      INTEGER PRIMARY KEY AUTOINCREMENT,
  workspace_key           TEXT NOT NULL DEFAULT 'current',
  group_key               TEXT NOT NULL,
  -- same_content | different_content | partial | deferred
  decision                TEXT NOT NULL DEFAULT 'deferred',
  representative_photo_id INTEGER REFERENCES photo_file(id) ON DELETE SET NULL,
  max_distance            INTEGER NOT NULL,
  member_count            INTEGER NOT NULL,
  created_at              TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at              TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (workspace_key, group_key)
);

CREATE TABLE IF NOT EXISTS similar_image_member (
  group_id          INTEGER NOT NULL REFERENCES similar_image_group(id) ON DELETE CASCADE,
  photo_id          INTEGER NOT NULL REFERENCES photo_file(id) ON DELETE CASCADE,
  is_representative INTEGER NOT NULL DEFAULT 0,
  -- [변경사유]: Phase 4.1 partial — 동일 subgroup_key = 같은 콘텐츠 묶음
  subgroup_key      TEXT,
  is_subgroup_rep   INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (group_id, photo_id)
);

CREATE INDEX IF NOT EXISTS idx_similar_member_photo ON similar_image_member(photo_id);

INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', '5');
