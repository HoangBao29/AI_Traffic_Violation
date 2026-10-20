"""
Script test nhanh ai_detector.py với ảnh hoặc video.
Chạy từ thư mục gốc dự án:
    python backend/test_detector.py --image bus.jpg
    python backend/test_detector.py --video videos/traffic.mp4
"""

import sys
import argparse
from pathlib import Path

# Đảm bảo import đúng
sys.path.insert(0, str(Path(__file__).parent))

from ai_detector import get_detector


def test_image(path: str):
    print(f"\n🖼  Đang phân tích ảnh: {path}")
    detector = get_detector()
    result = detector.detect_image(path)

    summary = result["summary"]
    print(f"\n📊 Kết quả:")
    print(f"   - Tổng vi phạm   : {summary['total_violations']}")
    print(f"   - Không MBH      : {summary['no_helmet_count']}")
    print(f"   - Xe chở 3+      : {summary['overloaded_motorcycle_count']}")
    print(f"\n🖼  Ảnh annotated   : {result['annotated_path']}")

    if result["violations"]:
        print(f"\n⚠  Chi tiết vi phạm:")
        for i, v in enumerate(result["violations"], 1):
            print(f"   {i}. [{v['type']}] {v['label']} (conf={v.get('conf', 0):.2f})")


def test_video(path: str, frame_skip: int = 3):
    print(f"\n🎬 Đang phân tích video: {path} (frame_skip={frame_skip})")
    detector = get_detector()

    def progress(cur, tot):
        if tot and cur % 50 == 0:
            print(f"   Frame {cur}/{tot} ({cur*100//tot}%)")

    result = detector.detect_video(path, frame_skip=frame_skip, progress_cb=progress)

    summary = result["summary"]
    print(f"\n📊 Kết quả:")
    print(f"   - Tổng frame     : {result['total_frames']}")
    print(f"   - FPS            : {result['fps']:.1f}")
    print(f"   - Tổng vi phạm   : {summary['total_violations']}")
    print(f"   - Không MBH      : {summary['no_helmet_count']}")
    print(f"   - Xe chở 3+      : {summary['overloaded_motorcycle_count']}")
    print(f"\n🎬 Video annotated  : {result['annotated_path']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test AI detector")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--image", help="Đường dẫn ảnh")
    group.add_argument("--video", help="Đường dẫn video")
    parser.add_argument("--frame-skip", type=int, default=3,
                        help="Bỏ qua N-1 frame mỗi N frame (mặc định: 3)")
    args = parser.parse_args()

    if args.image:
        test_image(args.image)
    else:
        test_video(args.video, frame_skip=args.frame_skip)
