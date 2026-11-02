import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tools import youtube_upload


def response(status=200, payload=None, headers=None):
    result = Mock(status_code=status, headers=headers or {})
    result.json.return_value = payload or {}
    return result


class YouTubeUploadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.token = self.root / "token.json"
        self.token.write_text(json.dumps({"client_id": "cid", "client_secret": "csec", "refresh_token": "rt"}),
                              encoding="utf-8")
        self.video = self.root / "clip.mp4"
        self.video.write_bytes(b"fake-mp4")

    def test_load_client_accepts_installed_app_json(self):
        path = self.root / "client.json"
        path.write_text(json.dumps({"installed": {"client_id": "cid", "client_secret": "csec"}}), encoding="utf-8")
        self.assertEqual(youtube_upload.load_client(path), ("cid", "csec"))

    def test_upload_is_private_by_default(self):
        with patch.object(youtube_upload, "access_token", return_value="at"), \
             patch.object(youtube_upload.requests, "post",
                          return_value=response(200, headers={"Location": "https://upload.example/session"})) as post, \
             patch.object(youtube_upload.requests, "put", return_value=response(200, {"id": "vid123"})):
            result = youtube_upload.upload_video(self.video, "タイトル", "概要", self.token)
        metadata = post.call_args.kwargs["json"]
        self.assertEqual(metadata["status"]["privacyStatus"], "private")
        self.assertFalse(metadata["status"]["selfDeclaredMadeForKids"])
        self.assertEqual(result, {"video_id": "vid123", "url": "https://youtu.be/vid123", "privacy": "private"})

    def test_public_needs_explicit_privacy(self):
        with patch.object(youtube_upload, "access_token", return_value="at"), \
             patch.object(youtube_upload.requests, "post",
                          return_value=response(200, headers={"Location": "https://upload.example/session"})) as post, \
             patch.object(youtube_upload.requests, "put", return_value=response(200, {"id": "vid9"})):
            youtube_upload.upload_video(self.video, "T", "D", self.token, privacy="public")
        self.assertEqual(post.call_args.kwargs["json"]["status"]["privacyStatus"], "public")

    def test_unknown_privacy_is_refused_before_any_request(self):
        with patch.object(youtube_upload.requests, "post") as post:
            with self.assertRaises(ValueError):
                youtube_upload.upload_video(self.video, "T", "D", self.token, privacy="friends")
        post.assert_not_called()

    def test_auth_url_asks_for_offline_access_and_upload_scope_only(self):
        url = youtube_upload.auth_url("cid", "st")
        self.assertIn("access_type=offline", url)
        self.assertIn("youtube.upload", url)
        self.assertNotIn("youtube.force-ssl", url)


if __name__ == "__main__":
    unittest.main()
