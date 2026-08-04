-- [변경사유]: Phase 4.1 — partial 서브그룹 (같은/다른 혼합)
-- subgroup_key: 같은 값 = 같은 콘텐츠 후보. solo-{photo_id} = 단독
-- is_representative: 해당 서브그룹(또는 same_content 그룹) 대표

ALTER TABLE similar_image_member ADD COLUMN subgroup_key TEXT;
ALTER TABLE similar_image_member ADD COLUMN is_subgroup_rep INTEGER NOT NULL DEFAULT 0;

INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', '6');
