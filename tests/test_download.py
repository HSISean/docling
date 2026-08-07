import unittest

from app import app, JOBS_ROOT


class DownloadRouteTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
