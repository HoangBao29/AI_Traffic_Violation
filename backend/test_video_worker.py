import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
import process_pending_videos as worker
from ai_detector import TrafficViolationDetector, video_output_profile


class VideoWorkerTests(unittest.TestCase):
    def test_video_output_profile_limits_resolution_and_frame_rate(self):
        self.assertEqual(
            video_output_profile(2160, 3840, 31.5789),
            (630, 1120, 31.5789 / 3, 3),
        )
        self.assertEqual(video_output_profile(640, 480, 12), (640, 480, 12, 1))

    def test_detect_video_writes_downscaled_lower_fps_result_and_preserves_duration(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.mp4"
            output = Path(temp_dir) / "annotated.mp4"
            writer = cv2.VideoWriter(
                str(source), cv2.VideoWriter_fourcc(*"mp4v"), 30, (1920, 1080)
            )
            self.assertTrue(writer.isOpened())
            for index in range(12):
                frame = np.full((1080, 1920, 3), index * 10, dtype=np.uint8)
                writer.write(frame)
            writer.release()

            detector = TrafficViolationDetector.__new__(TrafficViolationDetector)
            with patch.object(detector, "_process_frame", side_effect=lambda frame, **_: ([], frame)):
                result = detector.detect_video(
                    str(source), output_path=str(output), frame_skip=3
                )

            cap = cv2.VideoCapture(str(output))
            self.assertTrue(cap.isOpened())
            self.assertEqual(cap.get(cv2.CAP_PROP_FRAME_WIDTH), 1120)
            self.assertEqual(cap.get(cv2.CAP_PROP_FRAME_HEIGHT), 630)
            self.assertAlmostEqual(cap.get(cv2.CAP_PROP_FPS), 10)
            self.assertEqual(cap.get(cv2.CAP_PROP_FRAME_COUNT), 4)
            cap.release()
            self.assertAlmostEqual(
                (result["total_frames"] / 30),
                (4 / 10),
                delta=0.1,
            )

    def test_upload_result_rejects_files_over_storage_safe_limit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "oversized.mp4"
            output.write_bytes(b"too large")
            client = MagicMock()

            with patch.object(worker, "MAX_ANNOTATED_UPLOAD_BYTES", 4):
                with self.assertRaisesRegex(ValueError, "Video kết quả quá lớn"):
                    worker.upload_result(client, output, "annotated/oversized.mp4")

        client.storage.from_.assert_not_called()

    def test_transcode_browser_video_replaces_output_with_h264_mp4(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "annotated.mp4"
            output.write_bytes(b"fmp4")

            def fake_ffmpeg(args, **_kwargs):
                Path(args[-1]).write_bytes(b"h264-browser-compatible")
                return MagicMock(returncode=0, stderr="")

            with patch.object(worker, "ffmpeg_executable", return_value="ffmpeg.exe"), \
                 patch.object(worker.subprocess, "run", side_effect=fake_ffmpeg) as run:
                converted = worker.transcode_browser_video(output)

            self.assertEqual(converted, output)
            self.assertEqual(output.read_bytes(), b"h264-browser-compatible")
            self.assertIn("libx264", run.call_args.args[0])
            self.assertIn("yuv420p", run.call_args.args[0])
            self.assertIn("+faststart", run.call_args.args[0])
            self.assertFalse(output.with_name("annotated.h264.tmp.mp4").exists())

    def test_heartbeat_loop_refreshes_until_stopped(self):
        client = MagicMock()
        stop_event = threading.Event()
        thread = threading.Thread(
            target=worker.heartbeat_loop,
            args=(client, stop_event, 0.01),
        )

        thread.start()
        time.sleep(0.035)
        stop_event.set()
        thread.join(timeout=1)

        self.assertFalse(thread.is_alive())
        self.assertGreaterEqual(client.table.return_value.upsert.call_count, 1)

    def test_deduplicate_merges_consecutive_observations_and_keeps_best_confidence(self):
        detections = [
            {"type": "no_helmet", "frame": 3, "conf": 0.72, "box": [0, 0, 50, 50]},
            {"type": "no_helmet", "frame": 9, "conf": 0.91, "box": [2, 2, 52, 52]},
        ]

        events = worker.deduplicate_video_events(detections, fps=30, frame_skip=3)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["first_frame"], 3)
        self.assertEqual(events[0]["last_frame"], 9)
        self.assertEqual(events[0]["occurrence_count"], 2)
        self.assertEqual(events[0]["conf"], 0.91)
        self.assertEqual(worker.summarize_events(events)["total_violations"], 1)

    def test_deduplicate_keeps_different_objects_and_violation_types_separate(self):
        detections = [
            {"type": "no_helmet", "frame": 1, "box": [0, 0, 30, 30]},
            {"type": "no_helmet", "frame": 4, "box": [100, 100, 130, 130]},
            {"type": "red_light", "frame": 5, "box": [1, 1, 31, 31]},
        ]

        events = worker.deduplicate_video_events(detections, fps=30, frame_skip=1)

        self.assertEqual(len(events), 3)

    def test_deduplicate_does_not_merge_observations_outside_time_window(self):
        detections = [
            {"type": "no_helmet", "frame": 1, "box": [0, 0, 30, 30]},
            {"type": "no_helmet", "frame": 200, "box": [0, 0, 30, 30]},
        ]

        events = worker.deduplicate_video_events(detections, fps=30, frame_skip=1)

        self.assertEqual(len(events), 2)

    def test_deduplicate_uses_matching_plate_for_same_event(self):
        detections = [
            {"type": "no_helmet", "frame": 1, "box": [0, 0, 30, 30],
             "license_plate": "ab-1234"},
            {"type": "no_helmet", "frame": 20, "box": [100, 100, 130, 130],
             "license_plate": "AB1234"},
        ]

        events = worker.deduplicate_video_events(detections, fps=30, frame_skip=1)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["occurrence_count"], 2)

    def test_deduplicate_does_not_treat_unknown_plate_as_vehicle_identity(self):
        detections = [
            {"type": "no_helmet", "frame": 1, "box": [0, 0, 30, 30],
             "license_plate": "Chưa rõ"},
            {"type": "no_helmet", "frame": 10, "box": [100, 100, 130, 130],
             "license_plate": "Chưa rõ"},
        ]

        events = worker.deduplicate_video_events(detections, fps=30, frame_skip=1)

        self.assertEqual(len(events), 2)

    def test_download_video_uses_legacy_file_url_when_storage_path_is_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            client = MagicMock()
            client.storage.from_.return_value.download.return_value = b"video-data"
            record = {
                "file_url": "videos/test_video.mp4",
                "storage_path": None,
            }

            with patch.object(worker, "UPLOAD_FOLDER", Path(temp_dir)):
                downloaded = worker.download_video(record, client)

            self.assertEqual(downloaded.read_bytes(), b"video-data")
            client.storage.from_.assert_called_once_with("videos")
            client.storage.from_.return_value.download.assert_called_once_with(
                "videos/test_video.mp4"
            )

    def test_download_video_extracts_path_from_legacy_storage_url(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            client = MagicMock()
            client.storage.from_.return_value.download.return_value = b"video-data"
            record = {
                "file_url": (
                    "https://storage.example/storage/v1/object/sign/videos/"
                    "uploads/traffic%20camera.mp4?token=redacted"
                ),
                "storage_path": None,
            }

            with patch.object(worker, "UPLOAD_FOLDER", Path(temp_dir)):
                worker.download_video(record, client)

            client.storage.from_.return_value.download.assert_called_once_with(
                "uploads/traffic camera.mp4"
            )

    def test_process_one_forwards_stop_line_options_and_persists_completion(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "traffic.mp4"
            source.write_bytes(b"video")
            client = MagicMock()
            detector = MagicMock()
            detector.detect_video.return_value = {
                "violations": [],
                "summary": {"total_violations": 0},
            }
            record = {
                "id": 42,
                "processing_attempts": 1,
                "processing_options": {
                    "frame_skip": 4,
                    "stop_line_ratio": 0.64,
                    "stop_line_angle": -18,
                    "enable_stop_line": False,
                },
            }

            with patch.object(worker, "database", return_value=client), \
                 patch.object(worker, "download_video", return_value=source), \
                 patch.object(worker, "get_detector", return_value=detector), \
                 patch.object(worker, "transcode_browser_video", return_value=Path(tempfile.gettempdir()) / "converted.mp4"), \
                 patch.object(worker, "upload_result", return_value="annotated/result.mp4"):
                succeeded = worker.process_one(record)

        self.assertTrue(succeeded)
        self.assertEqual(detector.detect_video.call_args.kwargs["frame_skip"], 4)
        self.assertEqual(detector.detect_video.call_args.kwargs["stop_line_ratio"], 0.64)
        self.assertEqual(detector.detect_video.call_args.kwargs["stop_line_angle"], -18)
        self.assertFalse(detector.detect_video.call_args.kwargs["enable_stop_line"])
        video_updates = client.table.return_value.update.call_args_list
        self.assertTrue(any(call.args[0].get("status") == "processed" for call in video_updates))

    def test_process_one_marks_video_error_after_final_attempt(self):
        client = MagicMock()
        record = {"id": 42, "processing_attempts": 3}
        with patch.object(worker, "database", return_value=client), \
             patch.object(worker, "download_video", side_effect=OSError("missing upload")):
            succeeded = worker.process_one(record)

        self.assertFalse(succeeded)
        updates = client.table.return_value.update.call_args_list
        self.assertTrue(any(call.args[0].get("status") == "error" for call in updates))


if __name__ == "__main__":
    unittest.main()
