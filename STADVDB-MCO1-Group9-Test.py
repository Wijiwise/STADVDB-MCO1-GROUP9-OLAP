"""STADVDB-MCO1-Group9-Test.py — acceptance test suite for the DutchRail OLAP warehouse.

Run (PowerShell):
    $env:DUTCHRAIL_DB_PASSWORD = "your_password_here"
    .\\.venv\\Scripts\\python.exe STADVDB-MCO1-Group9-Test.py

Run (cmd.exe):
    set DUTCHRAIL_DB_PASSWORD=your_password_here
    .\\.venv\\Scripts\\python.exe STADVDB-MCO1-Group9-Test.py

Tests executed against the live railway_dw warehouse:
    T1 Connection     — MySQL reachable via the db.py connection pool
    T2 Schema         — Warehouse / Location / Train / Stop exist with required columns
    T3 Row counts     — estimated table sizes above minimum thresholds
    T4 Query checks   — all 5 analytical queries (KPIs, trend, cancellations,
                        ranking, heatmap) execute on a June 2019 slice and return
                        the expected columns and sane value ranges
    T5 Parameterization — bound values with quotes/injection literals are handled
                        safely (returned as data, never executed as SQL)

Exit code 0 = all tests passed; 1 = at least one failure.
"""

import sys
import time

import db

TEST_MONTH = ("2019-06-01", "2019-06-30")

REQUIRED_COLUMNS = {
    "Warehouse": [
        "service_rdt_id", "stop_rdt_id", "arrival_time", "arrival_cancelled",
        "departure_time", "departure_delay", "departure_cancelled",
    ],
    "Location": [
        "service_rdt_id", "stop_rdt_id", "station_name", "station_code", "arrival_delay",
    ],
    "Train": [
        "service_rdt_id", "train_number", "service_type", "service_date", "company",
    ],
    "Stop": [
        "stop_rdt_id", "station_name", "station_code", "completely_cancelled", "partly_cancelled",
    ],
}

MIN_ROWS = {
    "Warehouse": 15_000_000,
    "Location": 15_000_000,
    "Train": 1_500_000,
    "Stop": 15_000_000,
}

BASE_FROM = """
FROM Warehouse w
JOIN Location l ON l.service_rdt_id = w.service_rdt_id AND l.stop_rdt_id = w.stop_rdt_id
JOIN Train t ON t.service_rdt_id = w.service_rdt_id
JOIN Stop s ON s.stop_rdt_id = w.stop_rdt_id
""".strip()

CANCEL_EXPR = (
    "100.0 * AVG(CASE WHEN w.arrival_cancelled = 1 OR w.departure_cancelled = 1 "
    "THEN 1 ELSE 0 END)"
)

EVENT_TIME = "COALESCE(w.departure_time, w.arrival_time, TIMESTAMP(t.service_date))"

JUNE_WHERE = "WHERE t.service_date BETWEEN %s AND %s"
JUNE_PARAMS = list(TEST_MONTH)


class TestFailure(AssertionError):
    pass


def check(condition, message):
    if not condition:
        raise TestFailure(message)


def fmt_seconds(seconds):
    return f"{seconds:.1f}s"


def query_kpis():
    return f"""
SELECT COUNT(*) AS total_stops,
       AVG(l.arrival_delay) AS avg_arrival_delay,
       AVG(w.departure_delay) AS avg_departure_delay,
       {CANCEL_EXPR} AS cancel_rate
{BASE_FROM}
{JUNE_WHERE}
""".strip()


def query_trend():
    return f"""
SELECT period,
       AVG(arrival_delay_minutes) AS avg_arrival_delay,
       AVG(departure_delay_minutes) AS avg_departure_delay,
       100.0 * AVG(arrival_cancelled) AS arrival_cancel_rate,
       100.0 * AVG(departure_cancelled) AS departure_cancel_rate,
       COUNT(*) AS stop_count,
       MIN(y) AS y, MIN(q) AS q, MIN(m) AS m, MIN(d) AS d, MIN(h) AS h
FROM (
    SELECT DATE(et) AS period,
           YEAR(et) AS y, QUARTER(et) AS q, MONTH(et) AS m,
           DATE(et) AS d, HOUR(et) AS h,
           arrival_delay_minutes, departure_delay_minutes,
           arrival_cancelled, departure_cancelled
    FROM (
        SELECT {EVENT_TIME} AS et,
               l.arrival_delay AS arrival_delay_minutes,
               w.departure_delay AS departure_delay_minutes,
               w.arrival_cancelled AS arrival_cancelled,
               w.departure_cancelled AS departure_cancelled
        {BASE_FROM}
        {JUNE_WHERE}
    ) base
) levels
GROUP BY period
ORDER BY period
""".strip()


def query_cancellations():
    return f"""
SELECT t.company AS member,
       COUNT(*) AS stop_count,
       AVG(l.arrival_delay) AS avg_arrival_delay,
       AVG(w.departure_delay) AS avg_departure_delay,
       100.0 * AVG(w.arrival_cancelled) AS arrival_cancel_rate,
       100.0 * AVG(w.departure_cancelled) AS departure_cancel_rate,
       {CANCEL_EXPR} AS any_cancel_rate
{BASE_FROM}
{JUNE_WHERE}
GROUP BY member
ORDER BY any_cancel_rate DESC, stop_count DESC
""".strip()


def query_ranking():
    return f"""
SELECT t.train_number,
       t.company,
       t.service_type,
       COUNT(*) AS stop_count,
       AVG(l.arrival_delay) AS avg_arrival_delay,
       AVG(w.departure_delay) AS avg_departure_delay,
       GREATEST(COALESCE(AVG(l.arrival_delay), 0),
                COALESCE(AVG(w.departure_delay), 0)) AS severity_score,
       {CANCEL_EXPR} AS cancel_rate
{BASE_FROM}
{JUNE_WHERE}
GROUP BY t.train_number, t.company, t.service_type
ORDER BY severity_score DESC, stop_count DESC
LIMIT 10
""".strip()


def query_heatmap():
    return f"""
SELECT day_name, day_of_week, hour_of_day,
       AVG(arrival_delay_minutes) AS avg_arrival_delay,
       AVG(departure_delay_minutes) AS avg_departure_delay,
       100.0 * AVG(arrival_cancelled) AS arrival_cancel_rate,
       100.0 * AVG(departure_cancelled) AS departure_cancel_rate,
       COUNT(*) AS stop_count
FROM (
    SELECT DAYNAME({EVENT_TIME}) AS day_name,
           WEEKDAY({EVENT_TIME}) + 1 AS day_of_week,
           HOUR({EVENT_TIME}) AS hour_of_day,
           l.arrival_delay AS arrival_delay_minutes,
           w.departure_delay AS departure_delay_minutes,
           w.arrival_cancelled AS arrival_cancelled,
           w.departure_cancelled AS departure_cancelled
    {BASE_FROM}
    {JUNE_WHERE}
) base
GROUP BY day_name, day_of_week, hour_of_day
ORDER BY day_of_week, hour_of_day
""".strip()


def test_connection():
    version = db.test_connection()
    check(version, "empty MySQL version string")
    return f"MySQL {version}"


def test_schema():
    frame = db.run_query(
        """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = %s
          AND table_name IN ('Warehouse', 'Location', 'Train', 'Stop')
        """,
        (db.DB_NAME,),
    )
    frame.columns = [c.lower() for c in frame.columns]
    check(not frame.empty, f"no metadata found for schema {db.DB_NAME!r}")
    present = {table.lower(): set() for table in REQUIRED_COLUMNS}
    for row in frame.itertuples(index=False):
        present[row.table_name.lower()].add(row.column_name.lower())
    checked = 0
    for table, required in REQUIRED_COLUMNS.items():
        check(table.lower() in present, f"table {table} missing from schema {db.DB_NAME!r}")
        for column in required:
            check(
                column.lower() in present[table.lower()],
                f"column {table}.{column} missing",
            )
            checked += 1
    return f"4 tables, {checked} required columns verified"


def test_row_counts():
    frame = db.run_query(
        """
        SELECT table_name, table_rows
        FROM information_schema.tables
        WHERE table_schema = %s
          AND table_name IN ('Warehouse', 'Location', 'Train', 'Stop')
        """,
        (db.DB_NAME,),
    )
    frame.columns = [c.lower() for c in frame.columns]
    counts = {
        row.table_name.lower(): row.table_rows
        for row in frame.itertuples(index=False)
    }
    details = []
    for table, minimum in MIN_ROWS.items():
        actual = counts.get(table.lower())
        check(actual is not None, f"row estimate missing for {table}")
        check(
            actual >= minimum,
            f"{table}: {actual:,} rows < expected minimum {minimum:,}",
        )
        details.append(f"{table}~{actual:,}")
    return ", ".join(details) + " (information_schema estimates)"


def test_analytical_queries():
    cases = [
        (
            "kpis",
            query_kpis(),
            ["total_stops", "avg_arrival_delay", "avg_departure_delay", "cancel_rate"],
        ),
        (
            "trend",
            query_trend(),
            [
                "period", "avg_arrival_delay", "avg_departure_delay",
                "arrival_cancel_rate", "departure_cancel_rate", "stop_count",
                "y", "q", "m", "d", "h",
            ],
        ),
        (
            "cancellations",
            query_cancellations(),
            [
                "member", "stop_count", "avg_arrival_delay", "avg_departure_delay",
                "arrival_cancel_rate", "departure_cancel_rate", "any_cancel_rate",
            ],
        ),
        (
            "ranking",
            query_ranking(),
            [
                "train_number", "company", "service_type", "stop_count",
                "avg_arrival_delay", "avg_departure_delay", "severity_score", "cancel_rate",
            ],
        ),
        (
            "heatmap",
            query_heatmap(),
            [
                "day_name", "day_of_week", "hour_of_day", "avg_arrival_delay",
                "avg_departure_delay", "arrival_cancel_rate",
                "departure_cancel_rate", "stop_count",
            ],
        ),
    ]

    timings = []
    for name, sql, expected_columns in cases:
        started = time.perf_counter()
        frame = db.run_query(sql, JUNE_PARAMS)
        elapsed = time.perf_counter() - started
        timings.append((name, elapsed, len(frame)))

        check(not frame.empty, f"{name}: empty result for {TEST_MONTH[0]}..{TEST_MONTH[1]}")
        missing = set(expected_columns) - set(frame.columns)
        check(not missing, f"{name}: missing columns {sorted(missing)}")

        if name == "kpis":
            row = frame.iloc[0]
            check(int(row["total_stops"]) > 0, "kpis: total_stops is zero")
            check(
                0 <= float(row["cancel_rate"]) <= 100,
                f"kpis: cancel_rate out of range ({row['cancel_rate']})",
            )
        elif name == "trend":
            check(
                28 <= len(frame) <= 31,
                f"trend: expected ~30 daily periods for June, got {len(frame)}",
            )
        elif name == "cancellations":
            rates = frame["any_cancel_rate"].dropna()
            check(len(frame) >= 1, "cancellations: no companies returned")
            check(
                bool(((rates >= 0) & (rates <= 100)).all()),
                "cancellations: any_cancel_rate outside 0-100",
            )
        elif name == "ranking":
            check(len(frame) == 10, f"ranking: expected 10 rows (LIMIT 10), got {len(frame)}")
            check(
                bool((frame["severity_score"] >= 0).all()),
                "ranking: negative severity_score",
            )
        elif name == "heatmap":
            check(
                bool(frame["hour_of_day"].between(0, 23).all()),
                "heatmap: hour_of_day outside 0-23",
            )
            check(
                bool(frame["day_of_week"].between(1, 7).all()),
                "heatmap: day_of_week outside 1-7",
            )
            check(len(frame) <= 168, f"heatmap: {len(frame)} cells > 168")

    total = sum(elapsed for _, elapsed, _ in timings)
    detail = "; ".join(
        f"{name} {fmt_seconds(elapsed)} {rows} rows" for name, elapsed, rows in timings
    )
    return f"5/5 queries, total {fmt_seconds(total)} — {detail}"


def test_parameterization():
    ns_rows = int(
        db.run_query("SELECT COUNT(*) AS n FROM Train WHERE company = %s", ("NS",))
        .iloc[0]["n"]
    )
    check(ns_rows > 0, "parameterized filter company='NS' returned no rows")

    injection = db.run_query(
        "SELECT COUNT(*) AS n FROM Train WHERE company = %s",
        ("x' OR '1'='1",),
    )
    injection_rows = int(injection.iloc[0]["n"])
    check(
        injection_rows == 0,
        f"injection literal returned {injection_rows} rows (expected 0 — value was executed as data)",
    )

    comment_injection = db.run_query(
        "SELECT COUNT(*) AS n FROM Train WHERE company = %s",
        ("NS' --",),
    )
    check(
        int(comment_injection.iloc[0]["n"]) == 0,
        "comment-style literal unexpectedly matched rows",
    )
    return f"NS matched {ns_rows:,} services; injection literals bound as data (0 rows)"


TESTS = [
    ("T1 Connection", test_connection),
    ("T2 Schema", test_schema),
    ("T3 Row counts", test_row_counts),
    ("T4 Query checks", test_analytical_queries),
    ("T5 Parameterization", test_parameterization),
]


def main():
    print("=" * 70)
    print("STADVDB-MCO1-Group9 — OLAP Warehouse Test Suite")
    print(f"Target: {db.DB_USER}@{db.DB_HOST}:{db.DB_PORT}/{db.DB_NAME}")
    print(f"Test slice: {TEST_MONTH[0]} .. {TEST_MONTH[1]}")
    print("=" * 70)

    passed = 0
    failed = 0
    for label, fn in TESTS:
        started = time.perf_counter()
        try:
            detail = fn()
            elapsed = time.perf_counter() - started
            print(f"{label:24s} PASS  ({fmt_seconds(elapsed)})  {detail}")
            passed += 1
        except Exception as exc:
            elapsed = time.perf_counter() - started
            print(f"{label:24s} FAIL  ({fmt_seconds(elapsed)})  {exc}")
            failed += 1

    print("=" * 70)
    print(f"RESULT: {passed} passed, {failed} failed")
    print("=" * 70)
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
