import pandas as pd
from src.analysis_and_modeling import mask_salary_text, run_modeling_pipeline, run_primary_welch_test


def test_salary_text_masking_prevents_target_leakage():
    raw = "Compensation: $145,000 - $165k per year (155000 USD base)."
    masked = mask_salary_text(raw)
    assert "145,000" not in masked
    assert "165k" not in masked
    assert "155000 USD" not in masked
    assert "[SALARY_MASKED]" in masked


def test_welch_and_grouped_modeling_pipeline():
    rows = []
    for i in range(20):
        rows.append(
            {
                "job_id": i + 1,
                "source_job_id": 1000 + i,
                "first_run_id": 1,
                "normalized_employer": f"employer_{i}",
                "job_title": "Senior Python Engineer" if i % 2 == 0 else "Frontend React Developer",
                "job_excerpt": "Remote role with $150,000 salary.",
                "job_description": "Python AWS Docker" if i % 2 == 0 else "React TypeScript CSS",
                "job_geo": "USA",
                "job_level": "Senior",
                "job_type": "Full-Time",
                "job_industry": "Software Engineering",
                "salary_midpoint_usd": (170000.0 + (i * 1000)) if i % 2 == 0 else (130000.0 + (i * 1000)),
                "mentions_python": 1 if i % 2 == 0 else 0,
            }
        )
    df = pd.DataFrame(rows)
    welch = run_primary_welch_test(df)
    assert welch["python_employers_n"] == 10
    assert welch["non_python_employers_n"] == 10
    assert welch["mean_difference_usd"] == 39000.0

    res = run_modeling_pipeline(df, evaluate_holdout=False)
    assert res["holdout_evaluated"] is False
    assert res["dev_employers"] + res["test_employers"] == 20
