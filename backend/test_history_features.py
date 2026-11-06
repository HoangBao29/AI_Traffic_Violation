import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import app as app_module
from auth import ACTIVE_SESSIONS


class HistoryFeatureTests(unittest.TestCase):
    token = "history-feature-viewer-token"

    def setUp(self):
        ACTIVE_SESSIONS[self.token] = {
            "id": "viewer-1",
            "role": "viewer",
            "email": "viewer@example.com",
        }
        self.client = app_module.app.test_client()
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def tearDown(self):
        ACTIVE_SESSIONS.pop(self.token, None)

    def test_image_history_is_scoped_to_owner_and_end_date_is_inclusive(self):
        query = MagicMock()
        query.select.return_value = query
        query.order.return_value = query
        query.eq.return_value = query
        query.gte.return_value = query
        query.lt.return_value = query
        query.range.return_value = query
        query.execute.return_value = SimpleNamespace(data=[], count=0)
        admin = MagicMock()
        admin.table.return_value = query

        with patch.object(app_module, "get_admin_client", return_value=admin):
            response = self.client.get(
                "/image-analyses?start_date=2026-10-01&end_date=2026-10-02",
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        query.eq.assert_called_once_with("owner_id", "viewer-1")
        query.gte.assert_called_once_with("created_at", "2026-10-01T00:00:00+00:00")
        query.lt.assert_called_once_with("created_at", "2026-10-03T00:00:00+00:00")

    def test_image_history_rejects_invalid_or_reversed_dates(self):
        admin = MagicMock()
        with patch.object(app_module, "get_admin_client", return_value=admin):
            invalid = self.client.get(
                "/image-analyses?start_date=2026-13-01", headers=self.headers
            )
            reversed_dates = self.client.get(
                "/image-analyses?start_date=2026-10-03&end_date=2026-10-02",
                headers=self.headers,
            )

        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(reversed_dates.status_code, 400)

    def test_image_history_list_does_not_create_signed_urls_per_record(self):
        query = MagicMock()
        query.select.return_value = query
        query.order.return_value = query
        query.eq.return_value = query
        query.range.return_value = query
        query.execute.return_value = SimpleNamespace(
            data=[{
                "id": 23,
                "owner_id": "viewer-1",
                "file_name": "traffic.jpg",
                "annotated_storage_path": "image-analyses/23/annotated.jpg",
                "created_at": "2026-10-29T10:00:00+00:00",
                "summary": {"total_violations": 1},
            }],
            count=1,
        )
        admin = MagicMock()
        admin.table.return_value = query

        with patch.object(app_module, "get_admin_client", return_value=admin), \
             patch.object(app_module, "create_signed_storage_url") as sign_url:
            response = self.client.get("/image-analyses", headers=self.headers)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["items"][0]["id"], 23)
        self.assertNotIn("annotated_url", response.json["items"][0])
        sign_url.assert_not_called()

    def test_viewer_cannot_open_another_users_image_history(self):
        query = MagicMock()
        query.select.return_value = query
        query.eq.return_value = query
        query.limit.return_value = query
        query.execute.return_value = SimpleNamespace(data=[{
            "id": 8,
            "owner_id": "another-user",
            "original_storage_path": "original.jpg",
            "annotated_storage_path": "annotated.jpg",
        }])
        admin = MagicMock()
        admin.table.return_value = query

        with patch.object(app_module, "get_admin_client", return_value=admin), \
             patch.object(app_module, "create_signed_storage_url") as sign_url:
            response = self.client.get("/image-analyses/8", headers=self.headers)

        self.assertEqual(response.status_code, 404)
        sign_url.assert_not_called()

    def test_trend_endpoint_validates_bucket_and_returns_rpc_rows(self):
        admin = MagicMock()
        admin.rpc.return_value.execute.return_value.data = [
            {"bucket_start": "2026-10-01", "violation_type": "no_helmet", "violation_count": 2}
        ]
        with patch.object(app_module, "get_admin_client", return_value=admin):
            response = self.client.get(
                "/api/dashboard/trends?start_date=2026-10-01&end_date=2026-10-07&bucket=week",
                headers=self.headers,
            )
            invalid = self.client.get(
                "/api/dashboard/trends?start_date=2026-10-01&end_date=2026-10-07&bucket=month",
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["items"][0]["violation_count"], 2)
        self.assertEqual(invalid.status_code, 400)

    def test_viewer_cannot_access_admin_audit_logs(self):
        response = self.client.get("/api/audit-logs", headers=self.headers)
        self.assertEqual(response.status_code, 403)

    def test_health_reports_missing_service_role_key(self):
        anon_client = MagicMock()
        with patch.object(app_module, "get_admin_client", return_value=None), \
             patch.object(app_module, "supabase", anon_client):
            response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json["service_role_configured"])
        self.assertEqual(response.json["anon_database"], "ok")

    def test_health_distinguishes_invalid_service_key_from_valid_anon_key(self):
        anon_client = MagicMock()
        admin_client = MagicMock()
        admin_client.table.side_effect = Exception("Invalid API key")

        with patch.object(app_module, "get_admin_client", return_value=admin_client), \
             patch.object(app_module, "supabase", anon_client):
            response = self.client.get("/health")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json["anon_database"], "ok")
        self.assertEqual(response.json["service_role"], "invalid_api_key")
        self.assertEqual(response.json["error_code"], "SUPABASE_INVALID_API_KEY")

    def test_health_reports_missing_worker_heartbeat_schema_separately(self):
        anon_client = MagicMock()
        admin_client = MagicMock()
        admin_client.table.return_value.select.return_value.limit.return_value.execute.return_value = SimpleNamespace(
            data=[]
        )
        heartbeat_query = admin_client.table.return_value.select.return_value
        heartbeat_query.eq.return_value.limit.return_value.execute.side_effect = Exception(
            "Could not find the table 'public.worker_heartbeats' in the schema cache (PGRST205)"
        )

        with patch.object(app_module, "get_admin_client", return_value=admin_client), \
             patch.object(app_module, "supabase", anon_client):
            response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["database"], "ok")
        self.assertEqual(response.json["service_role"], "ok")
        self.assertEqual(response.json["video_worker"], "schema_missing")
        self.assertEqual(response.json["error_code"], "SUPABASE_WORKER_SCHEMA_MISSING")

    def test_video_violation_report_returns_signed_evidence(self):
        violation_query = MagicMock()
        violation_query.select.return_value = violation_query
        violation_query.eq.return_value = violation_query
        violation_query.limit.return_value = violation_query
        violation_query.execute.return_value = SimpleNamespace(data=[{
            "id": 22,
            "video_id": 7,
            "violation_type": "no_helmet",
            "label": "Không đội mũ bảo hiểm",
            "confidence": 0.9,
            "extra_data": "{}",
        }])
        video_query = MagicMock()
        video_query.select.return_value = video_query
        video_query.eq.return_value = video_query
        video_query.limit.return_value = video_query
        video_query.execute.return_value = SimpleNamespace(data=[{
            "file_name": "traffic.mp4",
            "file_url": None,
            "storage_path": "source/traffic.mp4",
            "annotated_url": "annotated/result.mp4",
            "upload_time": "2026-10-01T00:00:00+00:00",
        }])
        admin = MagicMock()
        admin.table.side_effect = [violation_query, video_query]

        with patch.object(app_module, "get_admin_client", return_value=admin), \
             patch.object(app_module, "create_signed_storage_url", return_value="https://signed.example/video"):
            response = self.client.get("/violations/22/report", headers=self.headers)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["violation"]["source_file_name"], "traffic.mp4")
        self.assertEqual(response.json["evidence"]["video_url"], "https://signed.example/video")


if __name__ == "__main__":
    unittest.main()
