import unittest
from unittest.mock import MagicMock

import analyze_commons_images as batch


class CommonsImageBatchTests(unittest.TestCase):
    def test_lists_only_supported_images_from_import_folder(self):
        storage = MagicMock()
        storage.list.return_value = [
            {"name": "photo.jpg", "id": "1"},
            {"name": "clip.webm", "id": "2"},
            {"name": "notes.txt", "id": "3"},
            {"name": "subfolder", "id": None},
        ]

        paths = batch.list_image_paths(storage, 10)

        self.assertEqual(paths, ["images/wikimedia/photo.jpg"])
        storage.list.assert_called_once_with(
            "images/wikimedia",
            options={"limit": 11, "offset": 0},
        )

    def test_list_respects_requested_limit(self):
        storage = MagicMock()
        storage.list.return_value = [
            {"name": f"{number}.jpg", "id": str(number)} for number in range(10)
        ]

        paths = batch.list_image_paths(storage, 3)

        self.assertEqual(paths, [
            "images/wikimedia/0.jpg",
            "images/wikimedia/1.jpg",
            "images/wikimedia/2.jpg",
        ])

    def test_login_requires_application_session_and_user(self):
        client = MagicMock()
        response = MagicMock()
        response.json.return_value = {"user": {"id": "viewer-id"}}
        response.is_error = False
        client.post.return_value = response

        with self.assertRaisesRegex(RuntimeError, "không trả về phiên người dùng"):
            batch._login(client, "http://127.0.0.1:5000", "viewer@example.com", "password")


if __name__ == "__main__":
    unittest.main()
