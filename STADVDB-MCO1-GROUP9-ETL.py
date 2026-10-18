import glob
import os
import sys
import time
import warnings

import pandas as pd
import pymysql

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
SOURCE_DB = os.getenv("SOURCE_DB", "railway_source")
WAREHOUSE_DB = os.getenv("WAREHOUSE_DB", "railway_dw")
CSV_GLOB = os.getenv("CSV_GLOB", "data/services-*.csv")
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "100000"))
LOCAL_TZ = "Europe/Amsterdam"
UNKNOWN_CODE = "UNKNOWN"

warnings.filterwarnings("ignore", category=UserWarning, message="pandas only supports SQLAlchemy")

CSV_COLUMNS = {
    "Service:RDT-ID": "service_rdt_id",
    "Service:Date": "service_date",
    "Service:Type": "service_type",
    "Service:Company": "service_company",
    "Service:Train number": "train_number",
    "Service:Completely cancelled": "completely_cancelled",
    "Service:Partly cancelled": "partly_cancelled",
    "Service:Maximum delay": "maximum_delay",
    "Stop:RDT-ID": "stop_rdt_id",
    "Stop:Station code": "station_code",
    "Stop:Station name": "station_name",
    "Stop:Arrival time": "arrival_time",
    "Stop:Arrival delay": "arrival_delay",
    "Stop:Arrival cancelled": "arrival_cancelled",
    "Stop:Departure time": "departure_time",
    "Stop:Departure delay": "departure_delay",
    "Stop:Departure cancelled": "departure_cancelled",
}

SOURCE_DDL = """
CREATE TABLE IF NOT EXISTS services (
    stop_rdt_id BIGINT NOT NULL,
    service_rdt_id BIGINT NOT NULL,
    service_date VARCHAR(10) NOT NULL,
    service_type VARCHAR(50),
    service_company VARCHAR(50),
    train_number INT,
    completely_cancelled VARCHAR(5),
    partly_cancelled VARCHAR(5),
    maximum_delay INT,
    station_code VARCHAR(20),
    station_name VARCHAR(120),
    arrival_time VARCHAR(32),
    arrival_delay INT,
    arrival_cancelled VARCHAR(5),
    departure_time VARCHAR(32),
    departure_delay INT,
    departure_cancelled VARCHAR(5),
    PRIMARY KEY (stop_rdt_id)
) ENGINE=InnoDB
"""

WAREHOUSE_DDL = [
    """
    CREATE TABLE IF NOT EXISTS Train (
        service_rdt_id BIGINT NOT NULL,
        train_number INT NOT NULL,
        service_type VARCHAR(50) NOT NULL,
        service_date DATE NOT NULL,
        company VARCHAR(50) NOT NULL,
        PRIMARY KEY (service_rdt_id)
    ) ENGINE=InnoDB
    """,
    """
    CREATE TABLE IF NOT EXISTS Stop (
        stop_rdt_id BIGINT NOT NULL,
        completely_cancelled TINYINT(1) NOT NULL,
        partly_cancelled TINYINT(1) NOT NULL,
        station_name VARCHAR(120) NOT NULL,
        station_code VARCHAR(20) NOT NULL,
        PRIMARY KEY (stop_rdt_id)
    ) ENGINE=InnoDB
    """,
    """
    CREATE TABLE IF NOT EXISTS Location (
        service_rdt_id BIGINT NOT NULL,
        stop_rdt_id BIGINT NOT NULL,
        station_name VARCHAR(120) NOT NULL,
        station_code VARCHAR(20) NOT NULL,
        arrival_delay INT NULL,
        PRIMARY KEY (service_rdt_id, stop_rdt_id),
        CONSTRAINT fk_location_train FOREIGN KEY (service_rdt_id) REFERENCES Train (service_rdt_id),
        CONSTRAINT fk_location_stop FOREIGN KEY (stop_rdt_id) REFERENCES Stop (stop_rdt_id)
    ) ENGINE=InnoDB
    """,
    """
    CREATE TABLE IF NOT EXISTS Warehouse (
        service_rdt_id BIGINT NOT NULL,
        stop_rdt_id BIGINT NOT NULL,
        arrival_time DATETIME NULL,
        arrival_cancelled TINYINT(1) NOT NULL DEFAULT 0,
        departure_time DATETIME NULL,
        departure_delay INT NULL,
        departure_cancelled TINYINT(1) NOT NULL DEFAULT 0,
        PRIMARY KEY (service_rdt_id, stop_rdt_id),
        CONSTRAINT fk_warehouse_location FOREIGN KEY (service_rdt_id, stop_rdt_id)
            REFERENCES Location (service_rdt_id, stop_rdt_id)
    ) ENGINE=InnoDB
    """,
]

SOURCE_INSERT = (
    "INSERT IGNORE INTO services (stop_rdt_id, service_rdt_id, service_date, service_type, "
    "service_company, train_number, completely_cancelled, partly_cancelled, maximum_delay, "
    "station_code, station_name, arrival_time, arrival_delay, arrival_cancelled, departure_time, "
    "departure_delay, departure_cancelled) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
)
SOURCE_ORDER = [
    "stop_rdt_id", "service_rdt_id", "service_date", "service_type", "service_company",
    "train_number", "completely_cancelled", "partly_cancelled", "maximum_delay", "station_code",
    "station_name", "arrival_time", "arrival_delay", "arrival_cancelled", "departure_time",
    "departure_delay", "departure_cancelled",
]

INSERT_TRAIN = (
    "INSERT IGNORE INTO Train (service_rdt_id, train_number, service_type, service_date, company) "
    "VALUES (%s,%s,%s,%s,%s)"
)
INSERT_STOP = (
    "INSERT IGNORE INTO Stop (stop_rdt_id, completely_cancelled, partly_cancelled, station_name, station_code) "
    "VALUES (%s,%s,%s,%s,%s)"
)
INSERT_LOCATION = (
    "INSERT IGNORE INTO Location (service_rdt_id, stop_rdt_id, station_name, station_code, arrival_delay) "
    "VALUES (%s,%s,%s,%s,%s)"
)
INSERT_WAREHOUSE = (
    "INSERT IGNORE INTO Warehouse (service_rdt_id, stop_rdt_id, arrival_time, arrival_cancelled, "
    "departure_time, departure_delay, departure_cancelled) VALUES (%s,%s,%s,%s,%s,%s,%s)"
)


def connect(database=None):
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=database,
        charset="utf8mb4",
        autocommit=False,
    )


def log(message):
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def to_rows(frame):
    cleaned = frame.astype(object).where(frame.notna(), None)
    return list(cleaned.itertuples(index=False, name=None))


def bulk_insert(conn, sql, frame):
    if frame.empty:
        return 0
    rows = to_rows(frame)
    with conn.cursor() as cur:
        cur.executemany(sql, rows)
    conn.commit()
    return len(rows)


def prepare_databases():
    with connect() as admin:
        with admin.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{SOURCE_DB}` CHARACTER SET utf8mb4")
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{WAREHOUSE_DB}` CHARACTER SET utf8mb4")
        admin.commit()
    with connect(SOURCE_DB) as conn:
        with conn.cursor() as cur:
            cur.execute(SOURCE_DDL)
        conn.commit()
    with connect(WAREHOUSE_DB) as conn:
        with conn.cursor() as cur:
            for ddl in WAREHOUSE_DDL:
                cur.execute(ddl)
        conn.commit()


def load_source():
    files = sorted(glob.glob(CSV_GLOB))
    if not files:
        sys.exit(f"No CSV files matched {CSV_GLOB}")
    conn = connect(SOURCE_DB)
    with conn.cursor() as cur:
        cur.execute("SET unique_checks=0")
    total = 0
    for path in files:
        log(f"Loading {path} into source database")
        reader = pd.read_csv(path, dtype=str, chunksize=CHUNK_SIZE, keep_default_na=True)
        for chunk in reader:
            chunk = chunk.rename(columns=CSV_COLUMNS)[SOURCE_ORDER]
            for column in ("train_number", "maximum_delay", "arrival_delay", "departure_delay"):
                chunk[column] = pd.to_numeric(chunk[column], errors="coerce").astype("Int64")
            total += bulk_insert(conn, SOURCE_INSERT, chunk)
        log(f"Source rows processed so far: {total:,}")
    conn.close()


def build_station_lookup(source_conn):
    lookup = pd.read_sql(
        "SELECT station_name, MIN(station_code) AS station_code "
        "FROM services WHERE station_code IS NOT NULL AND station_code <> '' "
        "GROUP BY station_name",
        source_conn,
    )
    return dict(zip(lookup["station_name"].str.strip(), lookup["station_code"].str.strip()))


def to_flag(series):
    return series.str.strip().str.lower().map({"true": 1, "false": 0}).fillna(0).astype("int64")


def to_local_datetime(series):
    parsed = pd.to_datetime(series, utc=True, errors="coerce")
    local = parsed.dt.tz_convert(LOCAL_TZ).dt.tz_localize(None)
    return local.dt.strftime("%Y-%m-%d %H:%M:%S").where(local.notna(), None)


def transform(raw, station_lookup):
    df = raw.copy()
    for column in ("station_name", "station_code", "service_type", "service_company"):
        df[column] = df[column].str.strip()
    df["station_code"] = df["station_code"].replace("", pd.NA)
    df["station_code"] = df["station_code"].fillna(df["station_name"].map(station_lookup))
    df["station_code"] = df["station_code"].fillna(UNKNOWN_CODE)
    df["service_type"] = df["service_type"].fillna("Unknown")
    df["service_company"] = df["service_company"].fillna("Unknown")
    df["service_date"] = pd.to_datetime(df["service_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    for column in ("completely_cancelled", "partly_cancelled", "arrival_cancelled", "departure_cancelled"):
        df[column] = to_flag(df[column])
    df["arrival_time"] = to_local_datetime(df["arrival_time"])
    df["departure_time"] = to_local_datetime(df["departure_time"])
    for column in ("arrival_delay", "departure_delay"):
        df[column] = pd.to_numeric(df[column], errors="coerce").astype("Int64")
    df = df.dropna(subset=["service_rdt_id", "stop_rdt_id", "service_date", "train_number", "station_name"])
    df = df.drop_duplicates(subset=["service_rdt_id", "stop_rdt_id"])

    train = df[["service_rdt_id", "train_number", "service_type", "service_date", "service_company"]]
    train = train.drop_duplicates(subset=["service_rdt_id"])
    stop = df[["stop_rdt_id", "completely_cancelled", "partly_cancelled", "station_name", "station_code"]]
    location = df[["service_rdt_id", "stop_rdt_id", "station_name", "station_code", "arrival_delay"]]
    warehouse = df[[
        "service_rdt_id", "stop_rdt_id", "arrival_time", "arrival_cancelled",
        "departure_time", "departure_delay", "departure_cancelled",
    ]]
    return train, stop, location, warehouse


def run_etl():
    source = connect(SOURCE_DB)
    target = connect(WAREHOUSE_DB)
    with target.cursor() as cur:
        cur.execute("SET unique_checks=0")
        cur.execute("SET foreign_key_checks=0")
    station_lookup = build_station_lookup(source)
    last_id = 0
    extracted = 0
    loaded = 0
    while True:
        raw = pd.read_sql(
            "SELECT * FROM services WHERE stop_rdt_id > %s ORDER BY stop_rdt_id LIMIT %s",
            source,
            params=(last_id, CHUNK_SIZE),
        )
        if raw.empty:
            break
        last_id = int(raw["stop_rdt_id"].max())
        extracted += len(raw)
        train, stop, location, warehouse = transform(raw, station_lookup)
        bulk_insert(target, INSERT_TRAIN, train)
        bulk_insert(target, INSERT_STOP, stop)
        bulk_insert(target, INSERT_LOCATION, location)
        loaded += bulk_insert(target, INSERT_WAREHOUSE, warehouse)
        log(f"Extracted {extracted:,} | loaded into Warehouse {loaded:,}")
    with target.cursor() as cur:
        cur.execute("SET foreign_key_checks=1")
    source.close()
    target.close()


def scalar(conn, sql):
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchone()[0]


def validate():
    source = connect(SOURCE_DB)
    target = connect(WAREHOUSE_DB)
    checks = []

    source_rows = scalar(source, "SELECT COUNT(*) FROM services")
    warehouse_rows = scalar(target, "SELECT COUNT(*) FROM Warehouse")
    checks.append(("Row count source vs Warehouse", source_rows, warehouse_rows))

    source_services = scalar(source, "SELECT COUNT(DISTINCT service_rdt_id) FROM services")
    train_rows = scalar(target, "SELECT COUNT(*) FROM Train")
    checks.append(("Distinct services vs Train rows", source_services, train_rows))

    checks.append(("Stop rows vs Warehouse rows", scalar(target, "SELECT COUNT(*) FROM Stop"), warehouse_rows))
    checks.append(("Location rows vs Warehouse rows", scalar(target, "SELECT COUNT(*) FROM Location"), warehouse_rows))

    source_delay = scalar(source, "SELECT COALESCE(SUM(departure_delay),0) FROM services")
    warehouse_delay = scalar(target, "SELECT COALESCE(SUM(departure_delay),0) FROM Warehouse")
    checks.append(("Sum of departure delay", int(source_delay), int(warehouse_delay)))

    source_arr = scalar(source, "SELECT COALESCE(SUM(arrival_delay),0) FROM services")
    warehouse_arr = scalar(target, "SELECT COALESCE(SUM(arrival_delay),0) FROM Location")
    checks.append(("Sum of arrival delay", int(source_arr), int(warehouse_arr)))

    source_cancel = scalar(source, "SELECT COUNT(*) FROM services WHERE departure_cancelled = 'true'")
    warehouse_cancel = scalar(target, "SELECT COUNT(*) FROM Warehouse WHERE departure_cancelled = 1")
    checks.append(("Departure cancellations", source_cancel, warehouse_cancel))

    orphans = scalar(
        target,
        "SELECT COUNT(*) FROM Warehouse w LEFT JOIN Location l "
        "ON w.service_rdt_id = l.service_rdt_id AND w.stop_rdt_id = l.stop_rdt_id "
        "WHERE l.stop_rdt_id IS NULL",
    )
    checks.append(("Orphan Warehouse rows", 0, orphans))

    unknown_codes = scalar(target, f"SELECT COUNT(*) FROM Stop WHERE station_code = '{UNKNOWN_CODE}'")
    log(f"Stops left with unresolved station code: {unknown_codes:,}")

    failed = 0
    for name, expected, actual in checks:
        status = "PASS" if expected == actual else "FAIL"
        if status == "FAIL":
            failed += 1
        log(f"{status} | {name} | expected={expected:,} actual={actual:,}")
    source.close()
    target.close()
    return failed


def main():
    started = time.time()
    prepare_databases()
    load_source()
    run_etl()
    failed = validate()
    log(f"Finished in {time.time() - started:,.1f}s with {failed} failed check(s)")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
