-- ============================================================
-- AI Traffic Violation Detection - Supabase Schema
-- Chạy file này trong Supabase SQL Editor
-- ============================================================

-- Bảng videos: lưu thông tin video đã upload
CREATE TABLE IF NOT EXISTS videos (
    id                  SERIAL PRIMARY KEY,
    filename            TEXT NOT NULL,
    filepath            TEXT NOT NULL,
    camera_id           TEXT DEFAULT 'unknown',
    location            TEXT DEFAULT 'unknown',
    status              TEXT DEFAULT 'pending'
                        CHECK (status IN ('pending', 'processing', 'processed', 'error')),
    annotated_filepath  TEXT,
    violation_count     INTEGER DEFAULT 0,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

-- Bảng violations: lưu từng vi phạm phát hiện được
CREATE TABLE IF NOT EXISTS violations (
    id          SERIAL PRIMARY KEY,
    video_id    INTEGER REFERENCES videos(id) ON DELETE CASCADE,
    type        TEXT NOT NULL
                CHECK (type IN ('no_helmet', 'overloaded_motorcycle')),
    label       TEXT NOT NULL,
    confidence  FLOAT DEFAULT 0,
    frame_index INTEGER,
    box         TEXT,      -- JSON string của [x1, y1, x2, y2]
    extra_data  TEXT,      -- dữ liệu bổ sung (số người, v.v.)
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Index để query nhanh
CREATE INDEX IF NOT EXISTS idx_videos_status     ON videos(status);
CREATE INDEX IF NOT EXISTS idx_violations_video  ON violations(video_id);
CREATE INDEX IF NOT EXISTS idx_violations_type   ON violations(type);

-- Trigger tự động cập nhật updated_at
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_videos_updated_at ON videos;
CREATE TRIGGER trg_videos_updated_at
    BEFORE UPDATE ON videos
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ============================================================
-- View: tóm tắt vi phạm theo video
-- ============================================================
CREATE OR REPLACE VIEW video_violation_summary AS
SELECT
    v.id,
    v.filename,
    v.camera_id,
    v.location,
    v.status,
    v.violation_count,
    v.created_at,
    COUNT(vi.id) FILTER (WHERE vi.type = 'no_helmet')            AS no_helmet_count,
    COUNT(vi.id) FILTER (WHERE vi.type = 'overloaded_motorcycle') AS overloaded_count
FROM videos v
LEFT JOIN violations vi ON vi.video_id = v.id
GROUP BY v.id;
