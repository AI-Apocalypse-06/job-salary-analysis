import collections
import datetime
import json
import re
import urllib.request

endpoints = [
    "https://jobicy.com/api/v2/remote-jobs?count=100&industry=dev",
    "https://jobicy.com/api/v2/remote-jobs?count=100",
    "https://jobicy.com/api/jobs?limit=100",
]

now_utc = datetime.datetime.now(datetime.timezone.utc)
print(f"Audit Timestamp (UTC): {now_utc.isoformat()}")


def fetch_json(url):
  req = urllib.request.Request(
      url, headers={"User-Agent": "Mozilla/5.0 (Academic-Feasibility-Check)"}
  )
  with urllib.request.urlopen(req, timeout=15) as resp:
    return resp.status, json.loads(resp.read().decode("utf-8"))


for ep in endpoints:
  try:
    status, payload = fetch_json(ep)
    print(f"\nEndpoint: {ep} -> HTTP {status}")
    print("Top-level keys:", list(payload.keys()))
    jobs = payload.get("jobs") or payload.get("data") or []
    print(f"Returned count: {len(jobs)}")
    if jobs:
      print("Sample keys:", list(jobs[0].keys()))
      inds = collections.Counter(
          str(j.get("jobIndustry")) for j in jobs
      ).most_common(5)
      geos = collections.Counter(
          str(j.get("jobGeo")) for j in jobs
      ).most_common(5)
      periods = collections.Counter(str(j.get("salaryPeriod")) for j in jobs)
      currencies = collections.Counter(
          str(j.get("salaryCurrency")) for j in jobs
      )
      print("Top industries:", inds)
      print("Top geos:", geos)
      print("Salary periods:", dict(periods))
      print("Salary currencies:", dict(currencies))
  except Exception as e:
    print(f"Endpoint {ep} failed: {e}")

# Run detailed eligibility filtering on the working endpoint
url = "https://jobicy.com/api/v2/remote-jobs?count=100"
status, raw_data = fetch_json(url)

with open("feasibility_snapshot_20261119.json", "w", encoding="utf-8") as f:
  json.dump(raw_data, f, ensure_ascii=False, indent=2)

jobs = raw_data.get("jobs", [])

IT_KEYWORDS = (
    "dev",
    "engineering",
    "software",
    "data",
    "sysadmin",
    "devops",
    "cyber",
    "security",
    "qa",
    "tech",
    "it",
    "ai",
    "machine learning",
)


def is_it_job(j):
  ind = str(j.get("jobIndustry", "")).lower()
  return any(k in ind for k in IT_KEYWORDS)


def is_valid_annual_usd(j):
  s_min = j.get("salaryMin")
  s_max = j.get("salaryMax")
  curr = str(j.get("salaryCurrency", "")).upper().strip()
  period = str(j.get("salaryPeriod", "")).lower().strip()
  try:
    s_min = float(s_min) if s_min is not None and s_min != "" else None
    s_max = float(s_max) if s_max is not None and s_max != "" else None
  except (ValueError, TypeError):
    return False
  return (
      s_min is not None
      and s_max is not None
      and 0 < s_min <= s_max
      and curr == "USD"
      and period in ("yearly", "annual", "year")
  )


def get_skills(j):
  text = f"{j.get('jobTitle','')} {j.get('jobExcerpt','')} {j.get('jobDescription','')}".lower()
  has_py = bool(re.search(r"\bpython\b", text))
  has_react = bool(re.search(r"\breact(?:\.js|js)?\b", text))
  return has_py, has_react


it_jobs = [j for j in jobs if is_it_job(j)]
it_anywhere = [
    j for j in it_jobs if "anywhere" in str(j.get("jobGeo", "")).lower()
]
it_all_valid_sal = [j for j in it_jobs if is_valid_annual_usd(j)]
it_anywhere_valid_sal = [j for j in it_anywhere if is_valid_annual_usd(j)]

pub_dates = collections.Counter(
    str(j.get("pubDate", ""))[:10] for j in it_jobs
)

print("\n--- COMBINED ELIGIBILITY COUNTS (UNFILTERED ENDPOINT) ---")
print(f"Total unique jobs returned: {len({j.get('id') for j in jobs})}")
print(f"1. IT Category eligible: {len(it_jobs)}")
print(f"2. IT + Geo='Anywhere' only: {len(it_anywhere)}")
print(
    "3a. IT + Valid Annual USD Both-Bounds Salary (All Geos):"
    f" {len(it_all_valid_sal)}"
)
print(
    "3b. IT + Geo='Anywhere' + Valid Annual USD Both-Bounds Salary:"
    f" {len(it_anywhere_valid_sal)}"
)

for label, subset in [
    ("IT All Geos + Valid Salary", it_all_valid_sal),
    ("IT Anywhere + Valid Salary", it_anywhere_valid_sal),
]:
  py_only, react_only, both, neither = 0, 0, 0, 0
  employers = set()
  for j in subset:
    py, rc = get_skills(j)
    if py and rc:
      both += 1
    elif py:
      py_only += 1
    elif rc:
      react_only += 1
    else:
      neither += 1
    employers.add(str(j.get("companyName", "")).strip().lower())
  print(f"\n[{label}] -> n={len(subset)}, Distinct Employers={len(employers)}")
  print(
      f"   Python-only mentions: {py_only} | React-only mentions: {react_only} |"
      f" Both: {both} | Neither: {neither}"
  )

print(
    "\nIT Listing Publication Dates (YYYY-MM-DD):",
    dict(sorted(pub_dates.items())),
)