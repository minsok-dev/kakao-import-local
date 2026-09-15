-- [변경사유]: 기존 로컬 DB에도 캡션 조회 인덱스 적용 (001은 신규만)
-- upload/run 시 ensure_caption_query_indexes 가 IF NOT EXISTS 로 실행

CREATE INDEX IF NOT EXISTS idx_assign_photo ON photo_message_assignment(photo_id);
CREATE INDEX IF NOT EXISTS idx_assign_group ON photo_message_assignment(group_id, message_id);
CREATE INDEX IF NOT EXISTS idx_exact_member_group ON exact_sha_member(group_id);
