import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import auth


class AuthTests(unittest.TestCase):
    def setUp(self):
        auth.ACTIVE_SESSIONS.clear()

    def test_public_registration_creates_viewer_without_storing_password(self):
        user = SimpleNamespace(
            id="user-1",
            email="viewer@example.com",
            user_metadata={"username": "viewer01", "full_name": "Viewer"},
            app_metadata={},
        )
        response = SimpleNamespace(user=user, session=object())
        client = MagicMock()
        client.auth.sign_up.return_value = response

        with patch.object(auth, "create_auth_client", return_value=client):
            info, token, error = auth.register_user(
                "Viewer01", "secret1", "Viewer", "viewer@example.com"
            )

        self.assertIsNone(error)
        self.assertEqual(info["role"], "viewer")
        self.assertNotIn("password", info)
        self.assertEqual(auth.ACTIVE_SESSIONS[token]["id"], "user-1")
        signup = client.auth.sign_up.call_args.args[0]
        self.assertEqual(signup["options"]["data"]["role"], "viewer")

    def test_registration_rejects_invalid_usernames_before_supabase_call(self):
        with patch.object(auth, "create_auth_client") as create_client:
            info, token, error = auth.register_user(
                "bad name", "secret1", "Viewer", "viewer@example.com"
            )

        self.assertIsNone(info)
        self.assertIsNone(token)
        self.assertIn("Tên đăng nhập", error)
        create_client.assert_not_called()

    def test_registration_explains_profile_trigger_failure(self):
        client = MagicMock()
        client.auth.sign_up.side_effect = Exception("Database error saving new user")

        with patch.object(auth, "create_auth_client", return_value=client):
            user, token, error = auth.register_user(
                "viewer03", "secret1", "Viewer", "viewer03@example.com"
            )

        self.assertIsNone(user)
        self.assertIsNone(token)
        self.assertIn("supabase_auth.sql", error)

    def test_registration_explains_duplicate_email(self):
        client = MagicMock()
        client.auth.sign_up.side_effect = Exception("User already registered")

        with patch.object(auth, "create_auth_client", return_value=client):
            user, token, error = auth.register_user(
                "viewer04", "secret1", "Viewer", "viewer04@example.com"
            )

        self.assertIsNone(user)
        self.assertIsNone(token)
        self.assertIn("đã được đăng ký", error)

    def test_login_ignores_user_editable_role_metadata(self):
        user = SimpleNamespace(
            id="user-2",
            email="viewer@example.com",
            user_metadata={
                "username": "viewer02",
                "full_name": "Viewer",
                "role": "admin",
            },
            app_metadata={},
        )
        client = MagicMock()
        client.auth.sign_in_with_password.return_value = SimpleNamespace(
            user=user, session=object()
        )
        client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = []

        with patch.object(auth, "create_auth_client", return_value=client):
            token, info, error = auth.authenticate("viewer@example.com", "secret1")

        self.assertTrue(token)
        self.assertIsNone(error)
        self.assertEqual(info["role"], "viewer")

    def test_login_reports_unconfirmed_email(self):
        client = MagicMock()
        client.auth.sign_in_with_password.side_effect = Exception("Email not confirmed")

        with patch.object(auth, "create_auth_client", return_value=client):
            token, user, error = auth.authenticate("viewer@example.com", "secret1")

        self.assertIsNone(token)
        self.assertIsNone(user)
        self.assertIn("chưa được xác minh", error)

    def test_admin_creation_requires_server_service_role_key(self):
        with patch.object(auth, "get_admin_client", return_value=None):
            info, error = auth.create_admin_user(
                "admin02", "secret1", "Admin", email="admin@example.com"
            )

        self.assertIsNone(info)
        self.assertIn("SUPABASE_SERVICE_ROLE_KEY", error)

    def test_admin_creation_uses_service_client_and_admin_role(self):
        user = SimpleNamespace(
            id="admin-1",
            email="admin@example.com",
            user_metadata={"username": "admin02", "full_name": "Admin"},
            app_metadata={"role": "admin"},
        )
        admin = MagicMock()
        admin.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = []
        admin.auth.admin.create_user.return_value = SimpleNamespace(user=user)

        with patch.object(auth, "get_admin_client", return_value=admin):
            info, error = auth.create_admin_user(
                "admin02", "secret1", "Admin", email="admin@example.com"
            )

        self.assertIsNone(error)
        self.assertEqual(info["role"], "admin")
        attributes = admin.auth.admin.create_user.call_args.args[0]
        self.assertEqual(attributes["app_metadata"]["role"], "admin")
        self.assertEqual(admin.table.return_value.update.call_count, 2)
        self.assertEqual(admin.table.call_args_list[-1].args[0], "users")
        users_update = admin.table.return_value.update.call_args_list[-1]
        self.assertEqual(users_update.args[0]["role"], "admin")


if __name__ == "__main__":
    unittest.main()
