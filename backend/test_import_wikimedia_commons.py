import io
import unittest
from email.message import Message
from unittest.mock import patch
from urllib.error import HTTPError

import import_wikimedia_commons as importer


class WikimediaApiTests(unittest.TestCase):
    def setUp(self):
        importer._last_api_request_at = 0.0
        importer._last_media_download_at = 0.0

    @patch.object(importer, "_api_get")
    def test_image_metadata_uses_thumbnail_url_and_size(self, api_get):
        api_get.return_value = {"query": {"pages": [{
            "title": "File:Traffic intersection.jpg",
            "pageid": 123,
            "imageinfo": [{
                "url": "https://upload.wikimedia.org/original.jpg",
                "thumburl": "https://upload.wikimedia.org/thumb/1280px-image.jpg",
                "size": 20_000_000,
                "thumbsize": 450_000,
                "mime": "image/jpeg",
                "extmetadata": {
                    "LicenseShortName": {"value": "CC BY 4.0"},
                    "LicenseUrl": {"value": "https://creativecommons.org/licenses/by/4.0/"},
                    "ImageDescription": {"value": "Traffic intersection"},
                },
            }],
        }]}}

        result = importer._get_media_info(["File:Traffic intersection.jpg"])

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["url"], "https://upload.wikimedia.org/thumb/1280px-image.jpg")
        self.assertEqual(result[0]["size"], 450_000)
        self.assertTrue(result[0]["is_thumbnail"])
        self.assertEqual(api_get.call_args.args[0]["iiurlwidth"], 1280)

    @patch.object(importer, "urlopen")
    @patch.object(importer.time, "sleep")
    def test_download_rate_limit_returns_clear_stop_error(self, sleep, urlopen):
        error = HTTPError(
            "https://upload.wikimedia.org/file.jpg",
            429,
            "Too Many Requests",
            Message(),
            io.BytesIO(b""),
        )
        urlopen.side_effect = error

        with self.assertRaisesRegex(importer.WikimediaDownloadRateLimit, "dừng các lượt tải tiếp theo"):
            importer._download({
                "title": "File:traffic.jpg",
                "url": "https://upload.wikimedia.org/file.jpg",
                "size": 1024,
            }, 1024 * 1024)
        self.assertEqual(urlopen.call_count, 1)

    @patch.object(importer, "urlopen")
    @patch.object(importer.time, "sleep")
    def test_download_accepts_official_thumbnail_host(self, sleep, urlopen):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.side_effect = [b"image-bytes", b""]
        urlopen.return_value = response
        importer._last_media_download_at = 0.0

        payload = importer._download({
            "title": "File:traffic.jpg",
            "url": "https://thumb.wikimedia.org/wikipedia/commons/thumb/a/a1/traffic.jpg/1280px-traffic.jpg",
            "size": 20,
            "is_thumbnail": True,
        }, 1024)

        self.assertEqual(payload, b"image-bytes")
        self.assertEqual(urlopen.call_count, 1)

    def test_download_rejects_non_wikimedia_host(self):
        with self.assertRaisesRegex(ValueError, "máy chủ media chính thức"):
            importer._download({
                "title": "File:traffic.jpg",
                "url": "https://evil.example/traffic.jpg",
                "size": 20,
            }, 1024)

    @patch.object(importer, "urlopen")
    @patch.object(importer.time, "sleep")
    def test_api_retries_rate_limit_then_returns_response(self, sleep, urlopen):
        headers = Message()
        headers["Retry-After"] = "7"
        rate_limit = HTTPError(
            importer.API_URL, 429, "Too Many Requests", headers, io.BytesIO(b"")
        )
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"query": {"search": []}}'
        urlopen.side_effect = [rate_limit, response]

        result = importer._api_get({"action": "query"})

        self.assertEqual(result, {"query": {"search": []}})
        sleep.assert_any_call(7.0)
        self.assertEqual(urlopen.call_count, 2)

    @patch.object(importer, "urlopen")
    @patch.object(importer.time, "sleep")
    def test_api_stops_after_maximum_rate_limit_retries(self, sleep, urlopen):
        headers = Message()
        headers["Retry-After"] = "1"
        rate_limit = HTTPError(
            importer.API_URL, 429, "Too Many Requests", headers, io.BytesIO(b"")
        )
        urlopen.side_effect = [rate_limit] * importer.API_MAX_ATTEMPTS

        with self.assertRaisesRegex(RuntimeError, "HTTP 429"):
            importer._api_get({"action": "query"})

        self.assertEqual(urlopen.call_count, importer.API_MAX_ATTEMPTS)

    @patch.object(importer, "urlopen")
    @patch.object(importer.time, "sleep")
    def test_api_retries_search_busy_response(self, sleep, urlopen):
        busy_response = unittest.mock.MagicMock()
        busy_response.__enter__.return_value.read.return_value = (
            b'{"error": {"code": "cirrussearch-too-busy-error"}}'
        )
        success_response = unittest.mock.MagicMock()
        success_response.__enter__.return_value.read.return_value = b'{"query": {}}'
        urlopen.side_effect = [busy_response, success_response]

        result = importer._api_get({"action": "query"})

        self.assertEqual(result, {"query": {}})
        sleep.assert_any_call(5)
        self.assertEqual(urlopen.call_count, 2)


if __name__ == "__main__":
    unittest.main()
