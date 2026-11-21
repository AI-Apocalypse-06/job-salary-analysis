# Remote IT Job Postings: Skills and Advertised Salary Analysis (UDE5014CM CW2)

Individual Data Science and Database Development Project evaluating skill mentions and advertised annual USD midpoint salaries across remote Software Engineering and QA & Testing vacancies on [Jobicy](https://jobicy.com).

## Attribution & Data Source
Primary data is collected via the public Jobicy API (`https://jobicy.com/api/v2/remote-jobs?count=100&industry=dev`) in accordance with Jobicy API terms permitting storage and processing with attribution. The assessed dataset represents a historical snapshot collected between 21 November 2026 and 4 December 2026 (Asia/Tashkent).

## Setup & Clean-Checkout Reproduction

1. Install dependencies:
   `python3 -m pip install -r requirements.txt`

2. Run automated tests (uses isolated temporary fixtures):
   `python3 -m pytest -v`

3. Run a live collection pass (initializes SQLite DB if absent and stores provenance):
   `python3 src/collector.py`
