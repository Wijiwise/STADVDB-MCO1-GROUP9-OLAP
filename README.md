# DutchRail OLAP Web Dashboard

An interactive, production-ready OLAP analytics web application built with **Streamlit**, connected directly to a local **MySQL 8.0+** data warehouse. The dashboard explores Dutch railway stop performance — delays, cancellations, and punctuality severity — through classic OLAP operations: **Slice**, **Dice**, **Roll-up**, and **Drill-down**.

---

## Project Overview

The application reads the warehouse produced by **`STADVDB-MCO1-GROUP9-ETL.py`** (source database `railway_source`, warehouse database **`railway_dw`**) and renders four business reports plus a KPI header, all driven by fully parameterized SQL executed live against MySQL.

### Warehouse model (as loaded by the ETL)

| Table | Key | Columns used by the dashboard |
|---|---|---|
| `Warehouse` | `(service_rdt_id, stop_rdt_id)` | `arrival_time`, `arrival_cancelled`, `departure_time`, `departure_delay`, `departure_cancelled` |
| `Location` | `(service_rdt_id, stop_rdt_id)` | `station_name`, `station_code`, `arrival_delay` |
| `Train` | `service_rdt_id` | `train_number`, `service_type`, `service_date`, `company` |
| `Stop` | `stop_rdt_id` | `station_name`, `station_code`, `completely_cancelled`, `partly_cancelled` |

The fact grain is one row per train stop per service. The dashboard joins:

```sql
FROM Warehouse w
JOIN Location l ON l.service_rdt_id = w.service_rdt_id AND l.stop_rdt_id = w.stop_rdt_id
JOIN Train t    ON t.service_rdt_id = w.service_rdt_id
JOIN Stop s     ON s.stop_rdt_id    = w.stop_rdt_id
```

**Derived attributes** (the ETL has no date dimension table, so time attributes are computed in SQL):

| Derived attribute | SQL expression |
|---|---|
| Event clock time | `COALESCE(w.departure_time, w.arrival_time, TIMESTAMP(t.service_date))` |
| Year / Quarter / Month / Day / Hour | `YEAR/QUARTER/MONTH/DATE/HOUR` of event time |
| Day name / day number | `DAYNAME(event_time)`, `WEEKDAY(event_time)+1` (1 = Monday) |
| Peak hour flag | Weekday **and** hour in `07–09` or `16–18` (rush-hour definition, adjustable in `app.py` as `PEAK_EXPR`) |

**Metric mapping:**

| Dashboard metric | Source column |
|---|---|
| Avg arrival delay | `Location.arrival_delay` |
| Avg departure delay | `Warehouse.departure_delay` |
| Arrival / departure cancelled % | `Warehouse.arrival_cancelled`, `Warehouse.departure_cancelled` |
| Cancellation rate (any) | stop cancelled on arrival **or** departure |
| Delay severity score | `GREATEST(avg arrival delay, avg departure delay)` |

**Reports delivered:**

1. **Delay Trend Over Time** — grouped bars (avg arrival/departure delay) with cancellation-rate lines on a secondary axis, at a selectable time grain.
2. **Cancellation Rates by Company** — grouped bars of arrival/departure cancellation % along the Company → Service Type → Train Number hierarchy.
3. **Delay Severity Ranking by Train** — Top-N trains ranked by severity score, with cancellation rate and stop counts.
4. **Hourly Delay Heatmap** — Day-of-week × Hour-of-day heat grid with a selectable metric.

---

## Prerequisites

- **Python 3.9+** (3.10/3.11 recommended)
- **MySQL 8.0+** running locally (or any reachable MySQL 8 instance)
- The `railway_dw` warehouse already loaded via `STADVDB-MCO1-GROUP9-ETL.py`
- pip and a terminal (PowerShell / Bash)

Verify your warehouse is populated before launching:

```sql
SELECT COUNT(*) FROM Warehouse;
SELECT COUNT(*) FROM Location;
SELECT COUNT(*) FROM Train;
SELECT COUNT(*) FROM Stop;
```

---

## Environment Setup

### 1. Clone / open the project folder

```powershell
cd "D:\MCO1 OLAP"
```

### 2. Create a virtual environment

```powershell
python -m venv .venv
```

Activation is **optional** — the commands below call the venv's executables by full path, which avoids PATH and execution-policy issues. If you prefer activating first:

```powershell
.\.venv\Scripts\Activate.ps1        # Windows PowerShell
# source .venv/bin/activate         # macOS / Linux
```

### 3. Install dependencies

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`requirements.txt` pins version bounds for `streamlit`, `pandas`, `plotly`, and `mysql-connector-python`.

### 4. Configure database credentials

Credentials are read from environment variables with sensible local defaults matching the ETL. **Never commit passwords** — set them in your shell session:

| Variable | Default | Purpose |
|---|---|---|
| `DUTCHRAIL_DB_HOST` (or `DB_HOST`) | `localhost` | MySQL host |
| `DUTCHRAIL_DB_PORT` (or `DB_PORT`) | `3306` | MySQL port |
| `DUTCHRAIL_DB_USER` (or `DB_USER`) | `root` | MySQL user |
| `DUTCHRAIL_DB_PASSWORD` (or `DB_PASSWORD`) | *(empty)* | MySQL password |
| `DUTCHRAIL_DB_NAME` (or `DB_NAME`) | `railway_dw` | Warehouse schema name |

PowerShell example:

```powershell
$env:DUTCHRAIL_DB_USER = "root"
$env:DUTCHRAIL_DB_PASSWORD = "your_password_here"
$env:DUTCHRAIL_DB_NAME = "railway_dw"
```

cmd.exe example:

```cmd
set DUTCHRAIL_DB_PASSWORD=your_password_here
```

> The connection module (`db.py`) uses a `mysql-connector-python` connection pool (size 5, session reset enabled) and exposes `test_connection()`, `run_query()`, and `reset_pool()`.
>
> **Note:** `STADVDB-MCO1-GROUP9-ETL.py` reads the same `DB_PASSWORD` variable — set it before running the ETL as well. The password is intentionally not hard-coded anywhere.

---

## Running the Application

Set the password for your shell session, then launch Streamlit through the venv's Python (**no activation required**):

**PowerShell:**

```powershell
$env:DUTCHRAIL_DB_PASSWORD = "your_password_here"
.\.venv\Scripts\python.exe -m streamlit run app.py
```

**cmd.exe:**

```cmd
set DUTCHRAIL_DB_PASSWORD=your_password_here
.\.venv\Scripts\python.exe -m streamlit run app.py
```

> If you activated the venv beforehand (`Activate.ps1` / `activate.bat`), plain `streamlit run app.py` also works. If you see `'streamlit' is not recognized`, the venv is not on your PATH — use the full-path form above. Remember: `$env:` variables are PowerShell syntax; in cmd.exe use `set VAR=value` instead.

Streamlit prints a local URL (default `http://localhost:8501`). Open it in a browser. Use the sidebar's **Test database connection** button to confirm connectivity — it prints the MySQL server version.

> **First load can take a while:** the warehouse holds ~21M rows per fact table while MySQL's `innodb_buffer_pool_size` may still be at the 128 MB default. See Troubleshooting for the buffer-pool recommendation.

### File map

| File | Role |
|---|---|
| `app.py` | Streamlit UI: sidebar filters, KPI cards, 4 OLAP reports, drill-down state machine, parameterized SQL builders |
| `db.py` | Secure MySQL connection pool + query helpers (env-var configured) |
| `STADVDB-MCO1-GROUP9-ETL.py` | ETL that builds `railway_source` + `railway_dw` (run this first) |
| `requirements.txt` | Dependency bounds |
| `README.md` | This document |

---

## Navigating the UI

### Sidebar — Slice & Dice controls

| Control | OLAP effect |
|---|---|
| **Service date range** | Slice on `Train.service_date` |
| **Stations** multi-select | Slice on `Location.station_name` |
| **Operating companies** multi-select | Slice on `Train.company` |
| **Service types** / **Train numbers** multi-select | Slice on `Train` attributes |
| **Hour type** (All / Peak only / Off-peak only) | Dice on the derived peak-hour flag (weekday 07–09 & 16–18) |
| **Minimum delay threshold** slider | Dice on fact metrics — keeps stops where `max(arrival, departure) delay >= N` |
| **Reset drill-downs** | Clears both drill paths and returns to default grains |

Every report re-queries MySQL with the combined filter set. The caption under the title shows the **active slice/dice context**.

> Note: the date-range slice filters on the train's **service date** (always populated), while charts aggregate each stop's **event clock time** (departure, else arrival). A departure delayed past midnight counts toward the next calendar day's hour but stays inside its service date.

### Main area — Roll-up / Drill-down controls

- **Time grain selector** (Report 1): `Year → Quarter → Month → Day → Hour`. Choosing a coarser grain performs a **roll-up**; a finer grain performs a **drill-down**.
- **Service hierarchy selector** (Report 2): `Company → Service Type → Train Number`.
- **Click-to-drill**: click any bar on Report 1 or Report 2 to descend one level *scoped to the clicked member* (e.g., click the 2024 bar → quarterly view of 2024 only). A breadcrumb shows the drill path; **Roll up one level** ascends one step.
- Every chart has a **View SQL** expander showing the exact parameterized query sent to MySQL.

---

## OLAP Features Breakdown

### Slice
Fix one or more dimension members while aggregating measures across the remaining cube. Implemented as `WHERE` predicates on dimension attributes — e.g., selecting station *Amsterdam Centraal* or company *NS* re-aggregates all reports for that member only.

### Dice
Simultaneous restriction on **two or more** dimensions, producing a sub-cube. Example: *Peak only* × companies {NS, Arriva} × date range Jan–Mar × delay ≥ 10 min. All sidebar filters compose conjunctively (AND), so any combination is a dice.

### Roll-up
Moving up a hierarchy decreases granularity and aggregates measures. Use the **Time grain** selector (`Hour → Day → Month → Quarter → Year`) or the **Service hierarchy** selector (`Train Number → Service Type → Company`), or press **Roll up one level**. SQL aggregation functions (`AVG`, `COUNT`, rate expressions) recompute at the coarser level. Time roll-ups are computed from the raw event timestamps (no pre-aggregated date table), which is the practical equivalent of rolling up a `dim_date_time` hierarchy.

### Drill-down
Moving down a hierarchy increases granularity. Either pick a finer level in the selectors, or **click a chart element** to drill into that member only — the clicked member's typed values are pushed into the SQL `WHERE` clause (e.g., clicking `2024-Q3` pins `YEAR(event_time) = 2024 AND QUARTER(event_time) = 9` while the grain advances to *Day*). Breadcrumbs make the current position in the cube explicit.

### Drill-across
The KPI header, trend chart, cancellation chart, ranking, and heatmap all share the **same filter context**, so changing a slice or drill path updates every measure area consistently — a practical demonstration of comparing multiple measures across the same dimension members.

---

## SQL & Security Notes

- All user selections are sent as **parameterized values** (`%s` placeholders via `mysql.connector`); dimension expressions are selected from a hardcoded whitelist (`TIME_EXPR`, `DIM_EXPR`) — no user text is interpolated into SQL structure.
- Grain-level drill predicates are built only from typed values (ints, ISO dates) parsed from chart click `customdata`.
- Credentials come exclusively from environment variables; nothing is hard-coded in source.
- The peak-hour flag and event-time fallback are defined once in `app.py` (`PEAK_EXPR`, `EVENT_TIME`) so the business rules are easy to adjust.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Could not connect to the MySQL data warehouse` | Confirm MySQL is running, `railway_dw` exists, and `DUTCHRAIL_DB_*` variables match your instance. Click **Test database connection** in the sidebar. |
| `Table 'railway_dw.Warehouse' doesn't exist` | Run the ETL first: `python STADVDB-MCO1-GROUP9-ETL.py` (loads `data/services-*.csv` into `railway_source` then builds `railway_dw`). Or set `DUTCHRAIL_DB_NAME` if your warehouse uses another schema. |
| Empty charts with "No … data for the current slice" | Widen the date range, clear multiselects, lower the delay threshold, or reset drill-downs. |
| Permission denied for user | Grant privileges: `GRANT SELECT ON railway_dw.* TO 'user'@'localhost';` |
| `'streamlit' is not recognized` | The venv is not on PATH — run `.\.venv\Scripts\python.exe -m streamlit run app.py`, or activate the venv first. |
| Queries take minutes / first load very slow | The warehouse is ~4.2 GB but `innodb_buffer_pool_size` defaults to 128 MB. In `my.ini` (e.g. `C:\ProgramData\MySQL\MySQL Server 9.3\my.ini`) set `innodb_buffer_pool_size=4G` under `[mysqld]` and restart the MySQL service. |
| Port in use (8501) | `.\.venv\Scripts\python.exe -m streamlit run app.py --server.port 8502` |
| Virtual environment not activating | Skip activation entirely and use the full-path commands above, or run `Set-ExecutionPolicy -Scope Process RemoteSigned` if script execution is blocked. |

### Expected schema (reference — created by the ETL)

```sql
CREATE TABLE Train (
    service_rdt_id BIGINT NOT NULL,
    train_number   INT NOT NULL,
    service_type   VARCHAR(50) NOT NULL,
    service_date   DATE NOT NULL,
    company        VARCHAR(50) NOT NULL,
    PRIMARY KEY (service_rdt_id)
) ENGINE=InnoDB;

CREATE TABLE Stop (
    stop_rdt_id          BIGINT NOT NULL,
    completely_cancelled TINYINT(1) NOT NULL,
    partly_cancelled     TINYINT(1) NOT NULL,
    station_name         VARCHAR(120) NOT NULL,
    station_code         VARCHAR(20) NOT NULL,
    PRIMARY KEY (stop_rdt_id)
) ENGINE=InnoDB;

CREATE TABLE Location (
    service_rdt_id BIGINT NOT NULL,
    stop_rdt_id    BIGINT NOT NULL,
    station_name   VARCHAR(120) NOT NULL,
    station_code   VARCHAR(20) NOT NULL,
    arrival_delay  INT NULL,
    PRIMARY KEY (service_rdt_id, stop_rdt_id),
    CONSTRAINT fk_location_train FOREIGN KEY (service_rdt_id) REFERENCES Train (service_rdt_id),
    CONSTRAINT fk_location_stop  FOREIGN KEY (stop_rdt_id)    REFERENCES Stop (stop_rdt_id)
) ENGINE=InnoDB;

CREATE TABLE Warehouse (
    service_rdt_id       BIGINT NOT NULL,
    stop_rdt_id          BIGINT NOT NULL,
    arrival_time         DATETIME NULL,
    arrival_cancelled    TINYINT(1) NOT NULL DEFAULT 0,
    departure_time       DATETIME NULL,
    departure_delay      INT NULL,
    departure_cancelled  TINYINT(1) NOT NULL DEFAULT 0,
    PRIMARY KEY (service_rdt_id, stop_rdt_id),
    CONSTRAINT fk_warehouse_location FOREIGN KEY (service_rdt_id, stop_rdt_id)
        REFERENCES Location (service_rdt_id, stop_rdt_id)
) ENGINE=InnoDB;
```

---

## Architecture at a glance

```
CSV files (data/services-*.csv)
        │
        ▼
STADVDB-MCO1-GROUP9-ETL.py ──► railway_source.services ──► railway_dw (Train, Stop, Location, Warehouse)
                                                              │
Browser (Streamlit UI)                                        │
        │  filter state / drill state                         │
        ▼                                                     │
app.py  ── builds parameterized SQL (slice, dice, roll-up, drill-down)
        │                                                     │
        ▼                                                     │
db.py   ── mysql-connector pool (localhost:3306 / railway_dw) ◄─┘
        │
        ▼
MySQL 8  Warehouse ⨝ Location ⨝ Train ⨝ Stop
```

Built for the DutchRail OLAP coursework project — interactive analytics over the proven ETL warehouse, without leaving the browser.
