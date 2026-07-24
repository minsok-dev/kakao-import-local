from kakao_import.config import load_settings
from kakao_import.db import connect

s = load_settings()
root = s.export_root
print("export_root", root)
max_bytes = 15 * 1024 * 1024
with connect(s.db_path) as conn:
    rows = conn.execute(
        """
        SELECT g.id, substr(g.sha256,1,12) AS sha, p.rel_path, p.file_name, p.byte_size, em.id AS mid
        FROM exact_sha_group g
        JOIN photo_file p ON p.id = g.representative_photo_id
        JOIN exact_sha_member em ON em.group_id = g.id AND em.is_representative = 1
        WHERE IFNULL(em.excluded_from_upload, 0) = 0
        ORDER BY g.id
        LIMIT 20
        """
    ).fetchall()
for r in rows:
    rel = str(r["rel_path"] or "").replace("\\", "/")
    fp = (root / rel) if root else None
    disk = fp.stat().st_size if fp and fp.is_file() else None
    size = disk if disk is not None else (r["byte_size"] or 0)
    flag = "TOO_BIG" if size > max_bytes else "ok"
    print(f"g={r['id']} mid={r['mid']} {flag} mb={size/1024/1024:.2f} sha={r['sha']} {r['file_name']}")
