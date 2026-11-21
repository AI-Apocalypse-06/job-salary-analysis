import sqlite3
from src.collector import evaluate_salary, init_db, normalize_employer


def test_valid_annual_usd_salary_calculation():
    job = {
        "salaryMin": 80000,
        "salaryMax": 120000,
        "salaryCurrency": "USD",
        "salaryPeriod": "yearly",
    }
    s_min, s_max, midpoint, curr, period, is_eligible, err = evaluate_salary(job)
    assert err is None
    assert is_eligible == 1
    assert midpoint == 100000.0
    assert curr == "USD"
    assert period == "yearly"


def test_invalid_salary_bounds_and_non_usd_separation():
    inverted = {
        "salaryMin": 120000,
        "salaryMax": 80000,
        "salaryCurrency": "USD",
        "salaryPeriod": "yearly",
    }
    _, _, midpoint, _, _, is_eligible, err = evaluate_salary(inverted)
    assert is_eligible == 0
    assert midpoint is None
    assert err == "salaryMin greater than salaryMax"

    eur_job = {
        "salaryMin": 60000,
        "salaryMax": 80000,
        "salaryCurrency": "EUR",
        "salaryPeriod": "yearly",
    }
    _, _, midpoint_eur, _, _, eligible_eur, err_eur = evaluate_salary(eur_job)
    assert err_eur is None
    assert eligible_eur == 0
    assert midpoint_eur is None


def test_database_schema_provenance_and_deduplication(tmp_path):
    db_file = tmp_path / "test_fixture.db"
    conn = sqlite3.connect(db_file)
    init_db(conn)

    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {
        "collection_runs",
        "companies",
        "jobs",
        "skills",
        "job_skills",
        "quarantine_records",
    }.issubset(tables)

    assert normalize_employer("Acme Corp., Inc.") == "acme"
    assert normalize_employer("ACME LLC") == "acme"
    conn.close()
