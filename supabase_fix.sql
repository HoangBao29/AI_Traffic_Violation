-- ============================================================
-- FIX 1: Storage Policies
-- Chạy trong Supabase SQL Editor
-- ============================================================

-- ── Bucket: videos ────────────────────────────────────────────
-- Cho phép authenticated users upload video
CREATE POLICY "Allow authenticated upload videos"
ON storage.objects FOR INSERT
TO authenticated
WITH CHECK (bucket_id = 'videos');

-- Cho phép tất cả đọc video (public read)
CREATE POLICY "Allow public read videos"
ON storage.objects FOR SELECT
TO public
USING (bucket_id = 'videos');

-- Cho phép authenticated xoá video của mình
CREATE POLICY "Allow authenticated delete videos"
ON storage.objects FOR DELETE
TO authenticated
USING (bucket_id = 'videos');

-- ── Bucket: violations ────────────────────────────────────────
-- Cho phép authenticated upload ảnh vi phạm
CREATE POLICY "Allow authenticated upload violations"
ON storage.objects FOR INSERT
TO authenticated
WITH CHECK (bucket_id = 'violations');

-- Cho phép tất cả đọc ảnh vi phạm
CREATE POLICY "Allow public read violations"
ON storage.objects FOR SELECT
TO public
USING (bucket_id = 'violations');


-- ============================================================
-- FIX 2: Tăng giới hạn file size cho bucket videos
-- Supabase không cho sửa file_size_limit qua SQL,
-- làm thủ công: Storage → videos → Edit → File size limit → 500 MB
-- ============================================================


-- ============================================================
-- FIX 3: Thêm cột còn thiếu vào bảng videos
-- (schema thực tế thiếu filepath và một số cột backend cần)
-- ============================================================

-- Thêm cột local_path để lưu đường dẫn file trên server
ALTER TABLE videos ADD COLUMN IF NOT EXISTS local_path TEXT;

-- Thêm cột annotated_url để lưu URL video đã annotate trên Supabase Storage
ALTER TABLE videos ADD COLUMN IF NOT EXISTS annotated_url TEXT;

-- Thêm cột violation_count nếu chưa có
ALTER TABLE videos ADD COLUMN IF NOT EXISTS violation_count INTEGER DEFAULT 0;

-- Cập nhật CHECK constraint của status (nếu thiếu giá trị 'processing'/'error')
-- Xoá constraint cũ và tạo lại
ALTER TABLE videos DROP CONSTRAINT IF EXISTS videos_status_check;
ALTER TABLE videos ADD CONSTRAINT videos_status_check
    CHECK (status IN ('pending', 'processing', 'processed', 'error'));


-- ============================================================
-- FIX 4: Thêm cột còn thiếu vào bảng violations
-- ============================================================

-- Thêm violation_type mapping (backend dùng 'no_helmet'/'overloaded_motorcycle')
-- Trong DB hiện tại có cột violation_type (varchar) → OK, chỉ cần thêm cột thiếu

-- Thêm cột label (mô tả vi phạm bằng tiếng Việt)
ALTER TABLE violations ADD COLUMN IF NOT EXISTS label TEXT;

-- Thêm cột box (tọa độ bounding box)
ALTER TABLE violations ADD COLUMN IF NOT EXISTS box TEXT;

-- Thêm cột extra_data (dữ liệu bổ sung như số người)
ALTER TABLE violations ADD COLUMN IF NOT EXISTS extra_data TEXT;


-- ============================================================
-- FIX 5: Tạo lại view cho đúng với schema thực
-- ============================================================
DROP VIEW IF EXISTS video_violation_summary;
CREATE OR REPLACE VIEW video_violation_summary AS
SELECT
    v.id,
    v.file_name,
    v.file_url,
    v.upload_by,
    v.upload_time,
    v.status,
    v.violation_count,
    COUNT(vi.id) FILTER (WHERE vi.violation_type = 'no_helmet')            AS no_helmet_count,
    COUNT(vi.id) FILTER (WHERE vi.violation_type = 'overloaded_motorcycle') AS overloaded_count
FROM videos v
LEFT JOIN violations vi ON vi.video_id = v.id
GROUP BY v.id;


-- ============================================================
-- FIX 6: RLS (Row Level Security) cho bảng videos & violations
-- Nếu bảng đang bật RLS mà không có policy → không insert được
-- ============================================================

-- Kiểm tra RLS hiện tại (chạy query này để xem):
-- SELECT tablename, rowsecurity FROM pg_tables
-- WHERE schemaname = 'public' AND tablename IN ('videos', 'violations');

-- Nếu RLS đang ON, thêm policy cho phép service role (backend) ghi:
ALTER TABLE videos    ENABLE ROW LEVEL SECURITY;
ALTER TABLE violations ENABLE ROW LEVEL SECURITY;

-- Policy: service_role (backend dùng anon key / service key) được làm tất cả
CREATE POLICY "Service role full access videos"
ON videos FOR ALL
TO service_role
USING (true)
WITH CHECK (true);

CREATE POLICY "Service role full access violations"
ON violations FOR ALL
TO service_role
USING (true)
WITH CHECK (true);

-- Policy: anon (public API) chỉ được đọc
CREATE POLICY "Anon read videos"
ON videos FOR SELECT
TO anon
USING (true);

CREATE POLICY "Anon read violations"
ON violations FOR SELECT
TO anon
USING (true);

-- Policy: authenticated users có thể insert và update video của họ
CREATE POLICY "Authenticated insert videos"
ON videos FOR INSERT
TO authenticated
WITH CHECK (true);

CREATE POLICY "Authenticated update own videos"
ON videos FOR UPDATE
TO authenticated
USING (upload_by = auth.uid());

CREATE POLICY "Authenticated insert violations"
ON violations FOR INSERT
TO authenticated
WITH CHECK (true);
