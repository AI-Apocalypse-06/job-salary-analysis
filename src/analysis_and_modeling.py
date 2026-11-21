import argparse
import json
from pathlib import Path
import re
import sqlite3

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats
import seaborn as sns
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error
from sklearn.model_selection import GridSearchCV, GroupKFold, GroupShuffleSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.linear_model import Ridge
from sklearn.tree import DecisionTreeRegressor

DB_PATH = Path("data/jobicy_assessed.db")
FIG_DIR = Path("reports/figures")
OUT_DIR = Path("reports")

SALARY_MASK_RE = re.compile(
    r"(\$\s?\d[\d,]*(?:\.\d+)?\s?[kKmM]?|\b\d{2,3}\s?[kK]\b|\b\d{4,7}\s?(?:usd|eur|gbp|cad)\b)",
    re.IGNORECASE,
)


def mask_salary_text(text: str) -> str:
    if not isinstance(text, str):
        return ""
    return SALARY_MASK_RE.sub(" [SALARY_MASKED] ", text)


def load_data(db_path: Path = DB_PATH):
    conn = sqlite3.connect(db_path)
    try:
        jobs_df = pd.read_sql_query(
            """
            SELECT
                j.*,
                c.normalized_employer,
                c.raw_company_name
            FROM jobs j
            JOIN companies c ON j.company_id = c.company_id
            """,
            conn,
        )
        skills_df = pd.read_sql_query(
            """
            SELECT js.job_id, s.skill_name
            FROM job_skills js
            JOIN skills s ON js.skill_id = s.skill_id
            """,
            conn,
        )
    finally:
        conn.close()

    if skills_df.empty:
        jobs_df["mentions_python"] = 0
    else:
        skill_pivot = (
            skills_df.assign(val=1)
            .pivot_table(index="job_id", columns="skill_name", values="val", fill_value=0)
            .reset_index()
        )
        jobs_df = jobs_df.merge(skill_pivot, on="job_id", how="left")
        for col in skill_pivot.columns:
            if col != "job_id":
                jobs_df[col] = jobs_df[col].fillna(0).astype(int)
        if "Python" in jobs_df.columns:
            jobs_df["mentions_python"] = jobs_df["Python"].astype(int)
        else:
            jobs_df["mentions_python"] = 0

    return jobs_df, skills_df


def run_primary_welch_test(eligible_df: pd.DataFrame) -> dict:
    # Earliest eligible listing per normalized_employer; lowest source_job_id breaks same-run ties
    dedup = (
        eligible_df.sort_values(by=["first_run_id", "source_job_id"], ascending=[True, True])
        .drop_duplicates(subset=["normalized_employer"], keep="first")
        .copy()
    )

    py_group = dedup.loc[dedup["mentions_python"] == 1, "salary_midpoint_usd"].to_numpy()
    non_py_group = dedup.loc[dedup["mentions_python"] == 0, "salary_midpoint_usd"].to_numpy()

    n1, n2 = len(py_group), len(non_py_group)
    mean1, mean2 = float(np.mean(py_group)), float(np.mean(non_py_group))
    sd1, sd2 = float(np.std(py_group, ddof=1)), float(np.std(non_py_group, ddof=1))
    diff = mean1 - mean2

    se_diff = float(np.sqrt((sd1**2 / n1) + (sd2**2 / n2)))
    denom = ((sd1**2 / n1) ** 2 / (n1 - 1)) + ((sd2**2 / n2) ** 2 / (n2 - 1))
    if denom == 0.0:
        df_welch = float(n1 + n2 - 2)
        t_stat, p_val = (float("inf"), 0.0) if diff != 0 else (0.0, 1.0)
        ci_low, ci_high = diff, diff
    else:
        df_welch = ((sd1**2 / n1) + (sd2**2 / n2)) ** 2 / denom
        t_stat, p_val = stats.ttest_ind(py_group, non_py_group, equal_var=False)
        t_crit = float(stats.t.ppf(0.975, df=df_welch))
        ci_low = diff - t_crit * se_diff
        ci_high = diff + t_crit * se_diff

    return {
        "python_employers_n": n1,
        "non_python_employers_n": n2,
        "python_mean_usd": round(mean1, 2),
        "non_python_mean_usd": round(mean2, 2),
        "mean_difference_usd": round(diff, 2),
        "ci_95_low_usd": round(ci_low, 2),
        "ci_95_high_usd": round(ci_high, 2),
        "welch_t_stat": round(float(t_stat), 4),
        "welch_df": round(float(df_welch), 2),
        "p_value": round(float(p_val), 4),
        "interpretation": (
            "Observational association between a Python mention and advertised annual USD salary "
            "midpoint among employers disclosing salary ranges (1 earliest listing per employer)."
        ),
    }


def generate_figures(jobs_df: pd.DataFrame, eligible_df: pd.DataFrame, skills_df: pd.DataFrame) -> None:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    sns.set_theme(style="whitegrid")
    attrib = "Source: Jobicy Public API (jobicy.com) | UDE5014CM Assessed Snapshot"

    # Figure 1: Daily Collection Yield
    daily = (
        jobs_df.groupby("first_seen_date_tashkent", as_index=False)
        .agg(new_unique=("job_id", "count"), new_eligible=("is_salary_eligible", "sum"))
        .sort_values("first_seen_date_tashkent")
    )
    daily["cum_unique"] = daily["new_unique"].cumsum()
    daily["cum_eligible"] = daily["new_eligible"].cumsum()

    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.plot(daily["first_seen_date_tashkent"], daily["cum_unique"], marker="o", label="Cumulative Unique IT Listings")
    ax.plot(daily["first_seen_date_tashkent"], daily["cum_eligible"], marker="s", label="Cumulative Salary-Eligible Listings")
    ax.axhline(350, color="gray", linestyle="--", linewidth=1, label="Target: 350 Unique")
    ax.axhline(100, color="tab:orange", linestyle=":", linewidth=1.2, label="Target: 100 Salary-Eligible")
    ax.set_title("Daily Collection Yield & Cumulative Progress (Asia/Tashkent)")
    ax.set_xlabel("First Seen Date (Tashkent)")
    ax.set_ylabel("Listing Count")
    ax.legend()
    fig.text(0.5, 0.01, attrib, ha="center", fontsize=8, color="dimgray")
    plt.tight_layout(rect=[0, 0.03, 1, 1])
    fig.savefig(FIG_DIR / "01_daily_collection_yield.png", dpi=200)
    plt.close(fig)

    # Figure 2: Skill Demand Frequency
    if not skills_df.empty:
        skill_counts = (
            skills_df.groupby("skill_name", as_index=False)["job_id"]
            .count()
            .rename(columns={"job_id": "mentions"})
            .sort_values("mentions", ascending=True)
        )
        fig, ax = plt.subplots(figsize=(8, 4.5))
        ax.barh(skill_counts["skill_name"], skill_counts["mentions"], color="steelblue")
        ax.set_title("Technical Skill Mentions Across Unique Remote IT Vacancies")
        ax.set_xlabel("Number of Listings Mentioning Skill")
        fig.text(0.5, 0.01, attrib, ha="center", fontsize=8, color="dimgray")
        plt.tight_layout(rect=[0, 0.03, 1, 1])
        fig.savefig(FIG_DIR / "02_skill_demand_frequency.png", dpi=200)
        plt.close(fig)

    # Figure 3: Employer-Deduplicated Python vs Non-Python Salary Distribution
    dedup = (
        eligible_df.sort_values(by=["first_run_id", "source_job_id"], ascending=[True, True])
        .drop_duplicates(subset=["normalized_employer"], keep="first")
        .copy()
    )
    dedup["Python Group"] = np.where(dedup["mentions_python"] == 1, "Mentions Python", "No Python Mention")
    fig, ax = plt.subplots(figsize=(7.5, 4.5))
    sns.boxplot(data=dedup, x="Python Group", y="salary_midpoint_usd", ax=ax, width=0.45, palette="Set2")
    sns.stripplot(data=dedup, x="Python Group", y="salary_midpoint_usd", ax=ax, color="black", alpha=0.6)
    ax.set_title("Employer-Deduplicated Annual USD Salary Midpoint by Python Mention")
    ax.set_xlabel("")
    ax.set_ylabel("Advertised Annual USD Midpoint ($)")
    fig.text(0.5, 0.01, attrib, ha="center", fontsize=8, color="dimgray")
    plt.tight_layout(rect=[0, 0.03, 1, 1])
    fig.savefig(FIG_DIR / "03_python_employer_salary_comparison.png", dpi=200)
    plt.close(fig)

    # Figure 4: Salary by Seniority Level
    fig, ax = plt.subplots(figsize=(8, 4.5))
    order = (
        eligible_df.groupby("job_level")["salary_midpoint_usd"]
        .median()
        .sort_values(ascending=False)
        .index
    )
    sns.boxplot(data=eligible_df, x="job_level", y="salary_midpoint_usd", order=order, ax=ax, palette="Blues_d")
    ax.set_title("Advertised Annual USD Salary Midpoint by Seniority Level")
    ax.set_xlabel("Job Level")
    ax.set_ylabel("Advertised Annual USD Midpoint ($)")
    plt.xticks(rotation=20, ha="right")
    fig.text(0.5, 0.01, attrib, ha="center", fontsize=8, color="dimgray")
    plt.tight_layout(rect=[0, 0.03, 1, 1])
    fig.savefig(FIG_DIR / "04_salary_by_seniority_geo.png", dpi=200)
    plt.close(fig)


def run_modeling_pipeline(eligible_df: pd.DataFrame, evaluate_holdout: bool = False) -> dict:
    df = eligible_df.copy()
    df["clean_text"] = (
        df["job_title"].fillna("").apply(mask_salary_text)
        + " "
        + df["job_excerpt"].fillna("").apply(mask_salary_text)
        + " "
        + df["job_description"].fillna("").apply(mask_salary_text)
    )

    cat_cols = ["job_geo", "job_level", "job_type", "job_industry"]
    for c in cat_cols:
        df[c] = df[c].fillna("Unknown").replace("", "Unknown")

    # Separate title-level exact-duplicate diagnostic check per employer
    exact_title_dups = int(df.duplicated(subset=["normalized_employer", "job_title"]).sum())

    # GroupShuffleSplit by normalized_employer so every listing from an employer stays in one partition
    gss = GroupShuffleSplit(n_splits=1, test_size=0.20, random_state=42)
    dev_idx, test_idx = next(gss.split(df, df["salary_midpoint_usd"], groups=df["normalized_employer"]))

    dev_df = df.iloc[dev_idx].copy()
    test_df = df.iloc[test_idx].copy()

    # Verify zero employer leakage between dev and test
    assert set(dev_df["normalized_employer"]).isdisjoint(set(test_df["normalized_employer"]))

    y_dev = dev_df["salary_midpoint_usd"].to_numpy()
    groups_dev = dev_df["normalized_employer"].to_numpy()

    preprocessor = ColumnTransformer(
        transformers=[
            (
                "text",
                TfidfVectorizer(max_features=250, stop_words="english", ngram_range=(1, 2)),
                "clean_text",
            ),
            (
                "cat",
                OneHotEncoder(handle_unknown="ignore"),
                cat_cols,
            ),
        ]
    )

    gkf = GroupKFold(n_splits=5)

    # 1. Training-Mean Baseline CV MAE
    baseline_maes = []
    for tr_i, va_i in gkf.split(dev_df, y_dev, groups_dev):
        pred_mean = np.full(len(va_i), np.mean(y_dev[tr_i]))
        baseline_maes.append(mean_absolute_error(y_dev[va_i], pred_mean))

    # 2. Ridge Regression tuned inside GroupKFold
    ridge_pipe = Pipeline([("prep", preprocessor), ("model", Ridge(random_state=42))])
    ridge_grid = GridSearchCV(
        ridge_pipe,
        param_grid={"model__alpha": [0.1, 1.0, 10.0, 50.0, 100.0]},
        cv=gkf,
        scoring="neg_mean_absolute_error",
    )
    ridge_grid.fit(dev_df, y_dev, groups=groups_dev)

    # 3. Decision Tree tuned inside GroupKFold
    tree_pipe = Pipeline([("prep", preprocessor), ("model", DecisionTreeRegressor(random_state=42))])
    tree_grid = GridSearchCV(
        tree_pipe,
        param_grid={
            "model__max_depth": [2, 3, 4, 6],
            "model__min_samples_leaf": [2, 4, 6],
        },
        cv=gkf,
        scoring="neg_mean_absolute_error",
    )
    tree_grid.fit(dev_df, y_dev, groups=groups_dev)

    report = {
        "total_eligible_listings": int(len(df)),
        "total_unique_employers": int(df["normalized_employer"].nunique()),
        "same_employer_exact_title_duplicates": exact_title_dups,
        "dev_listings": int(len(dev_df)),
        "dev_employers": int(dev_df["normalized_employer"].nunique()),
        "test_listings": int(len(test_df)),
        "test_employers": int(test_df["normalized_employer"].nunique()),
        "cv_5fold_grouped_mae_usd": {
            "baseline_mean": round(float(np.mean(baseline_maes)), 2),
            "ridge": round(float(-ridge_grid.best_score_), 2),
            "decision_tree": round(float(-tree_grid.best_score_), 2),
        },
        "best_params": {
            "ridge": ridge_grid.best_params_,
            "decision_tree": tree_grid.best_params_,
        },
        "holdout_evaluated": evaluate_holdout,
    }

    if evaluate_holdout:
        y_test = test_df["salary_midpoint_usd"].to_numpy()
        base_test_pred = np.full(len(y_test), float(np.mean(y_dev)))
        ridge_test_pred = ridge_grid.best_estimator_.predict(test_df)
        tree_test_pred = tree_grid.best_estimator_.predict(test_df)

        report["holdout_test_mae_usd"] = {
            "baseline_mean": round(float(mean_absolute_error(y_test, base_test_pred)), 2),
            "ridge": round(float(mean_absolute_error(y_test, ridge_test_pred)), 2),
            "decision_tree": round(float(mean_absolute_error(y_test, tree_test_pred)), 2),
        }

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="UDE5014CM Statistical Analysis & Modeling")
    parser.add_argument(
        "--evaluate-holdout",
        action="store_true",
        help="Run the single frozen evaluation on the held-out employer partition (use on/after 6 Dec 2026).",
    )
    args = parser.parse_args()

    jobs_df, skills_df = load_data()
    eligible_df = jobs_df[jobs_df["is_salary_eligible"] == 1].copy()

    welch_res = run_primary_welch_test(eligible_df)
    model_res = run_modeling_pipeline(eligible_df, evaluate_holdout=args.evaluate_holdout)
    generate_figures(jobs_df, eligible_df, skills_df)

    summary = {"welch_comparison": welch_res, "modeling": model_res}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = OUT_DIR / ("final_frozen_results.json" if args.evaluate_holdout else "checkpoint3_dev_results.json")
    out_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
