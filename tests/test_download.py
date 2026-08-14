import unittest

from app import JOBS_ROOT, _jobs, _jobs_lock, _new_job, app


class DownloadRouteTests(unittest.TestCase):
    def tearDown(self):
        with _jobs_lock:
            _jobs.clear()

    def test_download_works_for_output_files_with_spaces(self):
        client = app.test_client()
        job_id = "job-with-spaces"
        output_dir = JOBS_ROOT / job_id / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        target_name = "Redacted Report 01.pdf"
        target_path = output_dir / target_name
        target_path.write_bytes(b"test payload")

        response = client.get(f"/api/jobs/{job_id}/download/{target_name}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"test payload")

    def test_job_status_returns_active_job(self):
        client = app.test_client()
        _new_job("active-job", 2)

        response = client.get("/api/jobs/active-job")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["status"], "running")
        self.assertEqual(response.json["progress"], {"current": 0, "total": 2})

    def test_job_status_returns_not_found_for_unknown_job(self):
        client = app.test_client()

        response = client.get("/api/jobs/missing-job")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json["error"], "Unknown or expired job.")


if __name__ == "__main__":
    unittest.main()
