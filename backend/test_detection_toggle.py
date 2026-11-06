from io import BytesIO
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import app as app_module
from ai_detector import stop_line_y_at_x, vehicle_crossed_stop_line
from auth import ACTIVE_SESSIONS


class DetectionToggleTests(unittest.TestCase):
    def setUp(self):
        ACTIVE_SESSIONS["test-viewer-token"] = {
            "id": "viewer-id",
            "role": "viewer",
            "email": "viewer@example.com",
        }

    def tearDown(self):
        ACTIVE_SESSIONS.pop("test-viewer-token", None)

    def test_image_endpoint_passes_stop_line_toggle_to_detector(self):
        class DetectorStub:
            def __init__(self):
                self.enabled = None

            def detect_image(self, image_path, stop_line_ratio, enable_stop_line, stop_line_angle):
                self.enabled = enable_stop_line
                self.angle = stop_line_angle
                return {
                    "violations": [],
                    "annotated_path": str(Path(image_path).with_name("missing-output.jpg")),
                    "summary": {"total_violations": 0},
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            detector = DetectorStub()
            database = MagicMock()
            database.table.return_value.insert.return_value.execute.return_value.data = [
                {"id": 51, "created_at": "2026-10-24T00:00:00+00:00"}
            ]
            with patch.object(app_module, "UPLOAD_FOLDER", Path(temp_dir)), \
                 patch.object(app_module, "get_admin_client", return_value=database), \
                 patch.object(app_module, "upload_storage_file"), \
                 patch.object(app_module, "create_signed_storage_url", return_value="https://storage.example/signed"), \
                 patch.object(app_module, "write_audit_log"), \
                 patch.object(app_module, "get_detector", return_value=detector):
                response = app_module.app.test_client().post(
                    "/detect/image",
                    data={
                        "file": (BytesIO(b"image data"), "traffic.jpg"),
                        "stop_line_ratio": "0.64",
                        "stop_line_angle": "18",
                        "enable_stop_line": "false",
                    },
                    headers={"Authorization": "Bearer test-viewer-token"},
                    content_type="multipart/form-data",
                )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(detector.enabled)
        self.assertEqual(detector.angle, 18)

    def test_video_endpoint_passes_stop_line_toggle_to_detector(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = MagicMock()
            database.table.return_value.insert.return_value.execute.return_value.data = [{"id": 42}]
            with patch.object(app_module, "UPLOAD_FOLDER", Path(temp_dir)), \
                 patch.object(app_module, "get_admin_client", return_value=object()), \
                 patch.object(app_module, "database_client", return_value=database), \
                 patch.object(app_module, "upload_to_storage", return_value="https://storage/video"), \
                 patch.object(app_module, "write_audit_log"):
                response = app_module.app.test_client().post(
                    "/detect/video",
                    data={
                        "file": (BytesIO(b"video data"), "traffic.mp4"),
                        "stop_line_ratio": "0.64",
                        "stop_line_angle": "-12",
                        "enable_stop_line": "false",
                    },
                    headers={"Authorization": "Bearer test-viewer-token"},
                    content_type="multipart/form-data",
                )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json["video_id"], 42)
        queued_record = database.table.return_value.insert.call_args.args[0]
        self.assertFalse(queued_record["processing_options"]["enable_stop_line"])
        self.assertEqual(queued_record["processing_options"]["stop_line_ratio"], 0.64)
        self.assertEqual(queued_record["processing_options"]["stop_line_angle"], -12)

    def test_signed_video_upload_url_is_scoped_to_current_user_and_uses_storage_hostname(self):
        admin = MagicMock()
        admin.storage.from_.return_value.create_signed_upload_url.return_value = {
            "token": "short-lived-upload-token",
        }
        with patch.object(app_module, "get_admin_client", return_value=admin), \
             patch.object(app_module, "SUPABASE_URL", "https://project.supabase.co"):
            response = app_module.app.test_client().post(
                "/api/videos/upload-url",
                json={
                    "file_name": "traffic.mp4",
                    "file_size": 1024,
                    "frame_skip": 3,
                    "stop_line_ratio": 0.7,
                    "stop_line_angle": 15,
                    "enable_stop_line": True,
                    "camera_id": "camera-1",
                },
                headers={"Authorization": "Bearer test-viewer-token"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["storage_path"].split("/")[1], "viewer-id")
        self.assertEqual(response.json["upload_token"], "short-lived-upload-token")
        self.assertEqual(
            response.json["tus_endpoint"],
            "https://project.storage.supabase.co/storage/v1/upload/resumable/sign",
        )
        self.assertNotIn("api_key", response.json)
        admin.storage.from_.assert_called_once_with("videos")

    def test_video_queue_verifies_upload_and_persists_angle(self):
        admin = MagicMock()
        admin.table.return_value.insert.return_value.execute.return_value.data = [{"id": 73}]
        storage_path = "uploads/viewer-id/" + "a" * 32 + ".mp4"
        with patch.object(app_module, "get_admin_client", return_value=admin), \
             patch.object(app_module, "write_audit_log"):
            response = app_module.app.test_client().post(
                "/api/videos/queue",
                json={
                    "file_name": "traffic.mp4",
                    "storage_path": storage_path,
                    "frame_skip": 3,
                    "stop_line_ratio": 0.7,
                    "stop_line_angle": -20,
                    "enable_stop_line": True,
                    "camera_id": "camera-1",
                },
                headers={"Authorization": "Bearer test-viewer-token"},
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json["video_id"], 73)
        admin.storage.from_.return_value.info.assert_called_once_with(storage_path)
        inserted = admin.table.return_value.insert.call_args.args[0]
        self.assertEqual(inserted["processing_options"]["stop_line_angle"], -20)

    def test_video_queue_rejects_another_users_storage_path(self):
        response = app_module.app.test_client().post(
            "/api/videos/queue",
            json={
                "file_name": "traffic.mp4",
                "storage_path": "uploads/another-user/" + "a" * 32 + ".mp4",
            },
            headers={"Authorization": "Bearer test-viewer-token"},
        )

        self.assertEqual(response.status_code, 400)

    def test_stop_line_angle_changes_vehicle_crossing_side(self):
        self.assertAlmostEqual(stop_line_y_at_x(0, 100, 100, 0.5, 45), 0)
        self.assertTrue(vehicle_crossed_stop_line([20, 20, 30, 40], 100, 100, 0.5, 45))
        self.assertFalse(vehicle_crossed_stop_line([70, 20, 80, 60], 100, 100, 0.5, 45))

    def test_image_detection_rejects_guest(self):
        response = app_module.app.test_client().post("/detect/image")
        self.assertEqual(response.status_code, 401)

    def invalid_stop_line_ratio_request(self):
        response = app_module.app.test_client().post(
            "/detect/image",
            data={
                "file": (BytesIO(b"image data"), "traffic.jpg"),
                "stop_line_ratio": "1.2",
            },
            headers={"Authorization": "Bearer " + next(iter(ACTIVE_SESSIONS))},
            content_type="multipart/form-data",
        )
        return response

    def test_image_endpoint_rejects_invalid_stop_line_ratio(self):
        response = self.invalid_stop_line_ratio_request()
        self.assertEqual(response.status_code, 400)

    def test_legacy_public_storage_url_is_reduced_to_private_object_path(self):
        path = app_module.storage_path_from_url(
            "violations",
            "https://example.supabase.co/storage/v1/object/public/violations/annotated/camera%20one.mp4",
        )
        self.assertEqual(path, "annotated/camera one.mp4")

    def test_annotated_video_url_requires_login_and_returns_signed_link(self):
        database = MagicMock()
        database.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
            {"annotated_url": "annotated/result.mp4"}
        ]
        admin = MagicMock()
        admin.storage.from_.return_value.create_signed_url.return_value = {
            "signedURL": "https://storage.example/signed-result"
        }

        with patch.object(app_module, "database_client", return_value=database), \
             patch.object(app_module, "get_admin_client", return_value=admin):
            response = app_module.app.test_client().get(
                "/videos/42/annotated-url",
                headers={"Authorization": f"Bearer {next(iter(ACTIVE_SESSIONS))}"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["url"], "https://storage.example/signed-result")

    def test_csv_formula_values_are_neutralized(self):
        self.assertEqual(app_module.safe_csv_value("=HYPERLINK(\"x\")"), "'=HYPERLINK(\"x\")")
        self.assertEqual(app_module.safe_csv_value("regular note"), "regular note")


if __name__ == "__main__":
    unittest.main()
