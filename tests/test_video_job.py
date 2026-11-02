import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import video_job


JOB = {"id": "0123456789ab", "status": "approved", "topic": " 生成AIの最新ニュース ",
       "urls": ["https://source.example/a"], "created": "2026-11-03 10:00", "approved_at": "2026-11-03 10:05"}


class VideoJobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write_job(self, name, job):
        path = self.root / name
        path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        return path

    def test_only_approved_jobs_are_listed(self):
        self.write_job("0123456789ab.json", JOB)
        self.write_job("fedcba987654.json", {**JOB, "id": "fedcba987654", "status": "pending"})
        self.write_job("bad.json", {**JOB, "id": "../../x"})
        jobs = video_job.list_jobs(self.root)
        self.assertEqual([job["id"] for job in jobs], ["0123456789ab"])

    def test_non_https_source_is_rejected(self):
        path = self.write_job("0123456789ab.json", {**JOB, "urls": ["http://source.example/a"]})
        with self.assertRaises(ValueError):
            video_job.load_job(path)

    def test_plan_writes_request_without_calling_any_api(self):
        path = self.write_job("0123456789ab.json", JOB)
        out = self.root / "run-1"
        with patch.object(video_job.subprocess, "run") as run:
            request_path = video_job.write_plan(video_job.load_job(path), out)
        run.assert_not_called()
        request = json.loads(request_path.read_text(encoding="utf-8"))
        self.assertEqual(request["topic"], "生成AIの最新ニュース")
        self.assertEqual(request["provider"], "gemini")
        self.assertEqual(request["urls"], ["https://source.example/a"])
        self.assertEqual(request["job_id"], "0123456789ab")

    def test_plan_refuses_an_existing_output_folder(self):
        path = self.write_job("0123456789ab.json", JOB)
        out = self.root / "run-1"
        out.mkdir()
        with self.assertRaises(FileExistsError):
            video_job.write_plan(video_job.load_job(path), out)

    def test_prepare_needs_run_paid_flag(self):
        job = video_job.load_job(self.write_job("0123456789ab.json", JOB))
        with patch.object(video_job.subprocess, "run") as run:
            with self.assertRaises(SystemExit):
                video_job.run_prepare(job, self.root / "run-2", run_paid=False)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
