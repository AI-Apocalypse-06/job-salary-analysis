import datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import ssl
import urllib.error
import urllib.request
import zoneinfo

try:
  import certifi

  SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
  SSL_CONTEXT = ssl.create_default_context()

TASHKENT_TZ = zoneinfo.ZoneInfo("Asia/Tashkent")
BASE_URL = "https://jobicy.com/api/v2/remote-jobs?count=100&industry=dev"
DB_PATH = Path("data/jobicy_assessed.db")
RAW_DIR = Path("data/raw_snapshots")

SKILL_PATTERNS = {
    "Python": re.compile(r"\bpython\b", re.IGNORECASE),
    "React": re.compile(r"\breact(?:\.js|js)?\b", re.IGNORECASE),
    "TypeScript": re.compile(r"\btypescript\b", re.IGNORECASE),
    "JavaScript": re.compile(r"\bjavascript\b", re.IGNORECASE),
    "SQL": re.compile(r"\bsql\b", re.IGNORECASE),
    "AWS": re.compile(r"\baws\b|\bamazon web services\b", re.IGNORECASE),
    "Docker": re.compile(r"\bdocker\b", re.IGNORECASE),
    "Kubernetes": re.compile(r"\bkubernetes\b|\bk8s\b", re.IGNORECASE),
    "Go": re.compile(r"\bgolang\b|\bgo\b", re.IGNORECASE),
    "Java": re.compile(r"\bjava\b", re.IGNORECASE),
}


def normalize_employer(name: str) -> str:
  cleaned = re.sub(r"[^\w\s]", "", name.lower()).strip()
  cleaned = re.sub(
      r"\b(inc|llc|ltd|limited|corp|corporation|gmbh|co)\b", "", cleaned
  )
  return re.sub(r"\s+", " ", cleaned).strip()


def init_db(conn: sqlite3.Connection) -> None:
  conn.executescript("""
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS collection_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            endpoint_url TEXT NOT NULL,
            requested_at_tashkent TEXT NOT NULL,
            received_at_tashkent TEXT,
            http_status INTEGER,
            pages_fetched INTEGER DEFAULT 0,
            records_seen INTEGER DEFAULT 0,
            new_unique_inserted INTEGER DEFAULT 0,
            quarantined_count INTEGER DEFAULT 0,
            error_type TEXT,
            error_message TEXT
        );

        CREATE TABLE IF NOT EXISTS companies (
            company_id INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_company_name TEXT NOT NULL,
            normalized_employer TEXT NOT NULL UNIQUE
        );

        CREATE TABLE IF NOT EXISTS jobs (
            job_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_job_id INTEGER NOT NULL UNIQUE,
            company_id INTEGER NOT NULL,
            first_run_id INTEGER NOT NULL,
            last_run_id INTEGER NOT NULL,
            job_title TEXT NOT NULL,
            job_industry TEXT,
            job_geo TEXT,
            job_level TEXT,
            job_type TEXT,
            salary_min REAL,
            salary_max REAL,
            salary_midpoint_usd REAL,
            salary_currency TEXT,
            salary_period TEXT,
            is_salary_eligible INTEGER NOT NULL DEFAULT 0,
            source_url TEXT NOT NULL,
            published_at TEXT,
            first_seen_at_tashkent TEXT NOT NULL,
            last_seen_at_tashkent TEXT NOT NULL,
            first_seen_date_tashkent TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            job_excerpt TEXT,
            job_description TEXT,
            FOREIGN KEY (company_id) REFERENCES companies(company_id),
            FOREIGN KEY (first_run_id) REFERENCES collection_runs(run_id),
            FOREIGN KEY (last_run_id) REFERENCES collection_runs(run_id)
        );

        CREATE TABLE IF NOT EXISTS skills (
            skill_id INTEGER PRIMARY KEY AUTOINCREMENT,
            skill_name TEXT NOT NULL UNIQUE
        );

        CREATE TABLE IF NOT EXISTS job_skills (
            job_id INTEGER NOT NULL,
            skill_id INTEGER NOT NULL,
            PRIMARY KEY (job_id, skill_id),
            FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE,
            FOREIGN KEY (skill_id) REFERENCES skills(skill_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS quarantine_records (
            quarantine_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            raw_payload TEXT NOT NULL,
            failure_reason TEXT NOT NULL,
            quarantined_at_tashkent TEXT NOT NULL,
            FOREIGN KEY (run_id) REFERENCES collection_runs(run_id)
        );
    """)
  for skill_name in SKILL_PATTERNS:
    conn.execute(
        "INSERT OR IGNORE INTO skills (skill_name) VALUES (?)", (skill_name,)
    )
  conn.commit()


def evaluate_salary(raw_job: dict):
  s_min_raw = raw_job.get("salaryMin")
  s_max_raw = raw_job.get("salaryMax")
  curr = str(raw_job.get("salaryCurrency") or "").upper().strip() or None
  period = str(raw_job.get("salaryPeriod") or "").lower().strip() or None

  if (
      s_min_raw in (None, "")
      and s_max_raw in (None, "")
      and not curr
      and not period
  ):
    return None, None, None, None, None, 0, None

  try:
    s_min = float(s_min_raw) if s_min_raw not in (None, "") else None
    s_max = float(s_max_raw) if s_max_raw not in (None, "") else None
  except (ValueError, TypeError):
    return (
        None,
        None,
        None,
        curr,
        period,
        0,
        "Non-numeric salaryMin or salaryMax",
    )

  if (s_min is not None and s_min < 0) or (s_max is not None and s_max < 0):
    return (
        s_min,
        s_max,
        None,
        curr,
        period,
        0,
        "Negative salary bound",
    )

  if s_min is not None and s_max is not None and s_min > s_max:
    return (
        s_min,
        s_max,
        None,
        curr,
        period,
        0,
        "salaryMin greater than salaryMax",
    )

  if (
      s_min is not None
      and s_max is not None
      and 0 < s_min <= s_max
      and curr == "USD"
      and period in ("yearly", "annual", "year")
  ):
    midpoint = (s_min + s_max) / 2.0
    return s_min, s_max, midpoint, curr, period, 1, None

  return s_min, s_max, None, curr, period, 0, None


def run_collection(
    max_pages: int = 3,
    db_path: Path = DB_PATH,
    raw_dir: Path = RAW_DIR,
    base_url: str = BASE_URL,
) -> dict:
  db_path.parent.mkdir(parents=True, exist_ok=True)
  raw_dir.mkdir(parents=True, exist_ok=True)

  conn = sqlite3.connect(db_path)
  init_db(conn)

  req_dt = datetime.datetime.now(TASHKENT_TZ)
  req_iso = req_dt.isoformat()
  cur = conn.execute(
      "INSERT INTO collection_runs (endpoint_url, requested_at_tashkent)"
      " VALUES (?, ?)",
      (base_url, req_iso),
  )
  run_id = cur.lastrowid
  conn.commit()

  cursor = None
  pages_fetched = 0
  records_seen = 0
  new_inserted = 0
  quarantined = 0
  last_status = None

  try:
    while pages_fetched < max_pages:
      page_url = f"{base_url}&cursor={cursor}" if cursor else base_url
      req = urllib.request.Request(
          page_url,
          headers={
              "User-Agent": (
                  "UDE5014CM-StudentResearchCollector/1.0 (Attribution:"
                  " Jobicy.com)"
              )
          },
      )
      with urllib.request.urlopen(
          req, context=SSL_CONTEXT, timeout=20
      ) as resp:
        last_status = resp.status
        raw_bytes = resp.read()
        recv_dt = datetime.datetime.now(TASHKENT_TZ)
        recv_iso = recv_dt.isoformat()
        recv_date = recv_dt.strftime("%Y-%m-%d")

      snap_file = (
          raw_dir
          / f"run_{run_id:04d}_page_{pages_fetched + 1}_{recv_dt.strftime('%Y%m%dT%H%M%S')}.json"
      )
      snap_file.write_bytes(raw_bytes)

      payload = json.loads(raw_bytes.decode("utf-8"))
      jobs = payload.get("jobs", [])
      pages_fetched += 1

      # Process every item on the page in full
      for item in jobs:
        records_seen += 1
        item_bytes = json.dumps(item, sort_keys=True).encode("utf-8")
        item_hash = hashlib.sha256(item_bytes).hexdigest()

        source_id = item.get("id")
        title = str(item.get("jobTitle") or "").strip()
        company_raw = str(item.get("companyName") or "").strip()
        source_url = str(item.get("url") or "").strip()

        if (
            not isinstance(source_id, int)
            or not title
            or not company_raw
            or not source_url
        ):
          conn.execute(
              "INSERT INTO quarantine_records (run_id, raw_payload,"
              " failure_reason, quarantined_at_tashkent) VALUES (?, ?, ?, ?)",
              (
                  run_id,
                  json.dumps(item),
                  "Missing mandatory id, jobTitle, companyName, or url",
                  recv_iso,
              ),
          )
          quarantined += 1
          continue

        s_min, s_max, midpoint, curr, period, is_eligible, err_reason = (
            evaluate_salary(item)
        )
        if err_reason:
          conn.execute(
              "INSERT INTO quarantine_records (run_id, raw_payload,"
              " failure_reason, quarantined_at_tashkent) VALUES (?, ?, ?, ?)",
              (run_id, json.dumps(item), err_reason, recv_iso),
          )
          quarantined += 1
          continue

        norm_emp = normalize_employer(company_raw) or company_raw.lower()
        conn.execute(
            "INSERT OR IGNORE INTO companies (raw_company_name,"
            " normalized_employer) VALUES (?, ?)",
            (company_raw, norm_emp),
        )
        company_id = conn.execute(
            "SELECT company_id FROM companies WHERE normalized_employer = ?",
            (norm_emp,),
        ).fetchone()[0]

        industry = (
            ", ".join(item["jobIndustry"])
            if isinstance(item.get("jobIndustry"), list)
            else str(item.get("jobIndustry") or "")
        )
        existing = conn.execute(
            "SELECT job_id FROM jobs WHERE source_job_id = ?", (source_id,)
        ).fetchone()

        if existing:
          job_id = existing[0]
          conn.execute(
              "UPDATE jobs SET last_run_id = ?, last_seen_at_tashkent = ? WHERE"
              " job_id = ?",
              (run_id, recv_iso, job_id),
          )
        else:
          ins = conn.execute(
              """
                        INSERT INTO jobs (
                            source_job_id, company_id, first_run_id, last_run_id,
                            job_title, job_industry, job_geo, job_level, job_type,
                            salary_min, salary_max, salary_midpoint_usd,
                            salary_currency, salary_period, is_salary_eligible,
                            source_url, published_at, first_seen_at_tashkent,
                            last_seen_at_tashkent, first_seen_date_tashkent,
                            payload_hash, job_excerpt, job_description
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
              (
                  source_id,
                  company_id,
                  run_id,
                  run_id,
                  title,
                  industry,
                  str(item.get("jobGeo") or ""),
                  str(item.get("jobLevel") or ""),
                  str(item.get("jobType") or ""),
                  s_min,
                  s_max,
                  midpoint,
                  curr,
                  period,
                  is_eligible,
                  source_url,
                  str(item.get("pubDate") or ""),
                  recv_iso,
                  recv_iso,
                  recv_date,
                  item_hash,
                  str(item.get("jobExcerpt") or ""),
                  str(item.get("jobDescription") or ""),
              ),
          )
          job_id = ins.lastrowid
          new_inserted += 1

          combined_text = (
              f"{title} {item.get('jobExcerpt', '')}"
              f" {item.get('jobDescription', '')}"
          )
          for skill_name, pattern in SKILL_PATTERNS.items():
            if pattern.search(combined_text):
              skill_id = conn.execute(
                  "SELECT skill_id FROM skills WHERE skill_name = ?",
                  (skill_name,),
              ).fetchone()[0]
              conn.execute(
                  "INSERT OR IGNORE INTO job_skills (job_id, skill_id) VALUES"
                  " (?, ?)",
                  (job_id, skill_id),
              )

      conn.commit()
      if not payload.get("hasMore") or not payload.get("nextCursor"):
        break
      cursor = payload["nextCursor"]

    conn.execute(
        """
            UPDATE collection_runs
            SET received_at_tashkent = ?, http_status = ?, pages_fetched = ?,
                records_seen = ?, new_unique_inserted = ?, quarantined_count = ?
            WHERE run_id = ?
        """,
        (
            datetime.datetime.now(TASHKENT_TZ).isoformat(),
            last_status,
            pages_fetched,
            records_seen,
            new_inserted,
            quarantined,
            run_id,
        ),
    )
    conn.commit()

    total_unique = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    total_eligible = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE is_salary_eligible = 1"
    ).fetchone()[0]
    print(
        f"[Run {run_id}] Seen: {records_seen} | New Inserted: {new_inserted} |"
        f" Quarantined: {quarantined}"
    )
    print(
        f"Cumulative -> Unique IT Jobs: {total_unique} | Salary-Eligible:"
        f" {total_eligible}"
    )
    return {
        "run_id": run_id,
        "records_seen": records_seen,
        "new_unique_inserted": new_inserted,
        "quarantined_count": quarantined,
        "total_unique": total_unique,
        "total_eligible": total_eligible,
    }

  except Exception as exc:
    conn.execute(
        """
            UPDATE collection_runs
            SET received_at_tashkent = ?, http_status = ?, pages_fetched = ?,
                records_seen = ?, new_unique_inserted = ?, quarantined_count = ?,
                error_type = ?, error_message = ?
            WHERE run_id = ?
        """,
        (
            datetime.datetime.now(TASHKENT_TZ).isoformat(),
            last_status,
            pages_fetched,
            records_seen,
            new_inserted,
            quarantined,
            type(exc).__name__,
            str(exc),
            run_id,
        ),
    )
    conn.commit()
    print(f"[Run {run_id}] Error logged: {type(exc).__name__}: {exc}")
    raise
  finally:
    conn.close()


if __name__ == "__main__":
  run_collection(max_pages=3)