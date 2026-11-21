import json
import sqlite3
from unittest.mock import patch
from src.collector import init_db, run_collection


class DummyResponse:
    def __init__(self, payload: dict, status: int = 200):
        self._bytes = json.dumps(payload).encode("utf-8")
        self.status = status

    def read(self):
        return self._bytes

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False


def test_cross_run_deduplication_and_quarantine(tmp_path):
    db_path = tmp_path / "edge.db"
    raw_dir = tmp_path / "raw"

    run1_payload = {
        "hasMore": False,
        "jobs": [
            {
                "id": 200,
                "jobTitle": "Senior Python Engineer",
                "companyName": "Alpha Labs Inc.",
                "url": "https://jobicy.com/jobs/200",
                "salaryMin": 140000,
                "salaryMax": 160000,
                "salaryCurrency": "USD",
                "salaryPeriod": "yearly",
                "jobDescription": "Looking for Python and AWS experience.",
            },
            {
                "id": 100,
                "jobTitle": "Backend Developer",
                "companyName": "Alpha Labs LLC",
                "url": "https://jobicy.com/jobs/100",
                "salaryMin": 120000,
                "salaryMax": 140000,
                "salaryCurrency": "USD",
                "salaryPeriod": "yearly",
                "jobDescription": "Go and SQL stack.",
            },
            {
                "id": "corrupt-id",
                "jobTitle": "Broken Record",
                "companyName": "Bad Corp",
                "url": "https://jobicy.com/jobs/bad",
            },
        ],
    }

    with patch("urllib.request.urlopen", return_value=DummyResponse(run1_payload)):
        res1 = run_collection(max_pages=1, db_path=db_path, raw_dir=raw_dir)

    assert res1["records_seen"] == 3
    assert res1["new_unique_inserted"] == 2
    assert res1["quarantined_count"] == 1

    # Run 2 sees id=100 again plus a new id=300; must not duplicate id=100
    run2_payload = {
        "hasMore": False,
        "jobs": [
            run1_payload["jobs"][1],
            {
                "id": 300,
                "jobTitle": "QA Automation Engineer",
                "companyName": "Beta Systems",
                "url": "https://jobicy.com/jobs/300",
                "jobDescription": "Python pytest automation.",
            },
        ],
    }

    with patch("urllib.request.urlopen", return_value=DummyResponse(run2_payload)):
        res2 = run_collection(max_pages=1, db_path=db_path, raw_dir=raw_dir)

    assert res2["new_unique_inserted"] == 1
    assert res2["total_unique"] == 3

    conn = sqlite3.connect(db_path)
    # Verify same-run tie-break: Alpha Labs (id 100 vs 200 in run 1) selects lowest source_job_id (100)
    winner_id = conn.execute(
        """
        SELECT source_job_id
        FROM jobs j
        JOIN companies c ON j.company_id = c.company_id
        WHERE c.normalized_employer = 'alpha labs' AND j.is_salary_eligible = 1
        ORDER BY j.first_run_id ASC, j.source_job_id ASC
        LIMIT 1
        """
    ).fetchone()[0]
    assert winner_id == 100
    conn.close()
