-- Query 0: Daily Collection Yield Report (Initial Inventory + New IDs per Day)
SELECT
    first_seen_date_tashkent AS collection_date,
    COUNT(*) AS new_unique_jobs,
    SUM(is_salary_eligible) AS new_salary_eligible_jobs,
    SUM(COUNT(*)) OVER (ORDER BY first_seen_date_tashkent) AS cumulative_unique_jobs,
    SUM(SUM(is_salary_eligible)) OVER (ORDER BY first_seen_date_tashkent) AS cumulative_salary_eligible
FROM jobs
GROUP BY first_seen_date_tashkent
ORDER BY collection_date ASC;

-- Query 1: Stakeholder Audit - Collection Runs & Quarantine Summary
SELECT
    run_id,
    requested_at_tashkent,
    http_status,
    pages_fetched,
    records_seen,
    new_unique_inserted,
    quarantined_count,
    COALESCE(error_type, 'OK') AS status_flag
FROM collection_runs
ORDER BY run_id ASC;

-- Query 2: Salary Disclosure & Eligibility Breakdown
SELECT
    is_salary_eligible,
    COALESCE(salary_currency, 'NONE') AS currency,
    COALESCE(salary_period, 'NONE') AS period,
    COUNT(*) AS listing_count,
    ROUND(AVG(salary_midpoint_usd), 2) AS avg_midpoint_usd
FROM jobs
GROUP BY is_salary_eligible, currency, period
ORDER BY listing_count DESC;

-- Query 3: Skill Demand Frequency Across IT Vacancies
SELECT
    s.skill_name,
    COUNT(js.job_id) AS total_mentions,
    SUM(j.is_salary_eligible) AS salary_eligible_mentions,
    ROUND(100.0 * COUNT(js.job_id) / (SELECT COUNT(*) FROM jobs), 1) AS pct_of_all_jobs
FROM skills s
LEFT JOIN job_skills js ON s.skill_id = js.skill_id
LEFT JOIN jobs j ON js.job_id = j.job_id
GROUP BY s.skill_id, s.skill_name
ORDER BY total_mentions DESC, s.skill_name ASC;

-- Query 4: Primary Employer-Deduplicated Cohort (Earliest Run, Lowest source_job_id Tie-Break)
WITH ranked_employer_jobs AS (
    SELECT
        j.job_id,
        j.source_job_id,
        c.normalized_employer,
        j.job_title,
        j.salary_midpoint_usd,
        j.first_run_id,
        EXISTS (
            SELECT 1
            FROM job_skills js
            JOIN skills s ON js.skill_id = s.skill_id
            WHERE js.job_id = j.job_id AND s.skill_name = 'Python'
        ) AS mentions_python,
        ROW_NUMBER() OVER (
            PARTITION BY c.normalized_employer
            ORDER BY j.first_run_id ASC, j.source_job_id ASC
        ) AS rn
    FROM jobs j
    JOIN companies c ON j.company_id = c.company_id
    WHERE j.is_salary_eligible = 1
)
SELECT
    mentions_python,
    COUNT(*) AS unique_employers,
    ROUND(AVG(salary_midpoint_usd), 2) AS mean_salary_usd,
    ROUND(MIN(salary_midpoint_usd), 2) AS min_salary_usd,
    ROUND(MAX(salary_midpoint_usd), 2) AS max_salary_usd
FROM ranked_employer_jobs
WHERE rn = 1
GROUP BY mentions_python;

-- Query 5: Geographic & Seniority Salary Summary (Eligible Listings)
SELECT
    COALESCE(NULLIF(job_geo, ''), 'Unspecified') AS geography,
    COALESCE(NULLIF(job_level, ''), 'Unspecified') AS seniority_level,
    COUNT(*) AS eligible_listings,
    ROUND(AVG(salary_midpoint_usd), 2) AS mean_salary_usd
FROM jobs
WHERE is_salary_eligible = 1
GROUP BY geography, seniority_level
ORDER BY eligible_listings DESC, mean_salary_usd DESC;
