import io
import os
import unittest
from unittest.mock import Mock, patch

from bitrix_mcp import disk, read


class PortalConfigurationTests(unittest.TestCase):
    def test_cloud_and_self_hosted_origins_are_supported(self):
        for value, expected in [
            ("https://team.bitrix24.com", "https://team.bitrix24.com"),
            ("https://portal.example.org/", "https://portal.example.org"),
            ("https://portal.example.org:8443", "https://portal.example.org:8443"),
        ]:
            with self.subTest(value=value), patch.dict(os.environ, {"BITRIX_PORTAL_URL": value}):
                self.assertEqual(read._configured_portal(), expected)

    def test_credentials_and_routes_cannot_be_embedded_in_portal_setting(self):
        for value in [
            "",
            "http://portal.example.org",
            "https://user:password@portal.example.org",
            "https://portal.example.org/rest/1/key/",
            "https://portal.example.org?auth=key",
            "https://portal.example.org#fragment",
            "https://portal.example.org:invalid",
            "https://portal.\nexample.org",
            "https://",
            "https://portal.example.org/path",
        ]:
            with self.subTest(value=value), patch.dict(os.environ, {"BITRIX_PORTAL_URL": value}):
                with self.assertRaisesRegex(ValueError, "bitrix_https_portal_origin_required"):
                    read._configured_portal()

    def test_webhook_is_bound_to_configured_origin(self):
        with patch.object(read, "PORTAL", "https://portal.example.org"):
            for value in [
                "https://other.example.org/rest/1/synthetic-key/",
                "https://portal.example.org@other.example.org/rest/1/synthetic-key/",
                "https://portal.example.org/rest/1/synthetic-key/profile.json",
            ]:
                with self.subTest(value=value), patch.object(read, "_webhook", return_value=value):
                    with self.assertRaisesRegex(ValueError, "bitrix_base_webhook_required"):
                        read._connection()
            with patch.object(read, "_webhook", return_value="https://portal.example.org/rest/1/synthetic-key/"):
                self.assertEqual(
                    read._connection(), ("https://portal.example.org/rest/1/synthetic-key/", "synthetic-key")
                )

    def test_download_remains_bound_to_portal_and_exact_route(self):
        opener = Mock()
        opener.open.return_value = io.BytesIO(b"hello")
        with (
            patch.object(disk, "PORTAL", "https://portal.example.org:8443"),
            patch.object(disk, "build_opener", return_value=opener),
        ):
            result = disk._download(
                {"SIZE": "5", "DOWNLOAD_URL": "https://portal.example.org:8443/rest/download.json?auth=fixture"},
                "text",
                "synthetic-key",
            )
            self.assertEqual(result["content"], "hello")
            for url in [
                "https://other.example.org/rest/download.json",
                "https://portal.example.org/rest/download.json",
                "https://portal.example.org:8443/rest/1/key/profile.json",
            ]:
                with self.subTest(url=url), self.assertRaisesRegex(RuntimeError, "bitrix_download_route_not_allowed"):
                    disk._download({"SIZE": "5", "DOWNLOAD_URL": url}, "text", "synthetic-key")
