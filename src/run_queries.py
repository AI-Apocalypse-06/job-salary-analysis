from pathlib import Path
import sqlite3

DB_PATH = Path("data/jobicy_assessed.db")
SQL_PATH = Path("sql/queries.sql")


def run_all_queries(db_path: Path = DB_PATH, sql_path: Path = SQL_PATH) -> None:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found at {db_path}. Run src/collector.py first.")
    sql_text = sql_path.read_text(encoding="utf-8")
    statements = [s.strip() for s in sql_text.split(";") if s.strip()]

    conn = sqlite3.connect(db_path)
    try:
        for idx, stmt in enumerate(statements):
            first_line = stmt.splitlines()[0]
            print(f"\n=== {first_line} ===")
            cur = conn.execute(stmt)
            cols = [d[0] for d in cur.description]
            print(" | ".join(cols))
            print("-" * (len(" | ".join(cols)) + 4))
            rows = cur.fetchall()
            if not rows:
                print("(0 rows)")
            for row in rows:
                print(" | ".join(str(val) for val in row))
    finally:
        conn.close()


if __name__ == "__main__":
    run_all_queries()
