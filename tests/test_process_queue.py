import tempfile
import unittest
from pathlib import Path

import process_queue


class ProcessQueueDiscoveryTests(unittest.TestCase):
    def test_discover_pending_jobs_skips_auxiliary_json_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending = Path(tmp)
            (pending / "job-1.json").write_text("{}", encoding="utf-8")
            (pending / "job-1.payload.json").write_text("{}", encoding="utf-8")
            (pending / "job-1.progress.json").write_text("{}", encoding="utf-8")
            (pending / "job-2.json").write_text("{}", encoding="utf-8")

            original_pending = process_queue.PENDING
            try:
                process_queue.PENDING = pending
                jobs = process_queue._discover_pending_jobs(10)
            finally:
                process_queue.PENDING = original_pending

            self.assertEqual([p.name for p in jobs], ["job-1.json", "job-2.json"])

    def test_discover_pending_jobs_honors_max_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending = Path(tmp)
            for idx in range(5):
                (pending / f"job-{idx}.json").write_text("{}", encoding="utf-8")

            original_pending = process_queue.PENDING
            try:
                process_queue.PENDING = pending
                jobs = process_queue._discover_pending_jobs(3)
            finally:
                process_queue.PENDING = original_pending

            self.assertEqual(len(jobs), 3)


if __name__ == "__main__":
    unittest.main()
