# Technical Report — DutchRail OLAP Web Dashboard

**Course:** STADVDB — Multimedia Databases / Data Warehousing
**Stack:** Python 3.14 · Streamlit 1.66 · MySQL 9.3 · mysql-connector-python 9.7
**Dataset:** NS (Nederlandse Spoorwegen) train-stop records, full year **2019** (20,773,804 stop records)

---

## 1. Main Purpose of the Application

The DutchRail OLAP Web Dashboard is an **interactive decision-support application** that lets rail-operations analysts explore a large historical warehouse of Dutch railway stop records — 20.8 million stops across 15 operating companies — without writing SQL. The application translates classic OLAP operations (slice, dice, roll-up, drill-down) into parameterized SQL executed live against a MySQL data warehouse, and renders the results as interactive charts.

Its purpose follows the classical data-warehouse goal of providing *"a single version of the truth"* for analytical decision making [2][3]: the ETL pipeline (`STADVDB-MCO1-GROUP9-ETL.py`) cleans raw NS open-data CSV extracts into a normalized warehouse (`railway_dw`), and the dashboard becomes the analytical front-end over that curated layer.

### 1.1 Decision-making and analytical tasks supported

| # | Analytical task | Decision supported | Report(s) used |
|---|---|---|---|
| 1 | **Punctuality monitoring over time** — how do average arrival/departure delays and cancellation rates evolve daily/quarterly/hourly? | When to schedule preventive maintenance, staffing, and recovery buffers; impact assessment of disruption days | Report 1 (Trend) |
| 2 | **Operator performance comparison** — which operating companies cancel or delay the most? | Contract monitoring and penalty discussions with concession holders (NS, Arriva, Breng, etc.) | Report 2 (Cancellations) |
| 3 | **Service-level diagnosis** — which individual train services are the worst performers? | Targeted timetable or rolling-stock intervention for specific train numbers | Report 3 (Severity ranking) |
| 4 | **Peak-hour profiling** — when during the day/week do delays concentrate? | Allocation of surge capacity and staff during rush hours; validation of peak-hour policies | Report 4 (Heatmap) |

These tasks match the *monitoring*, *control*, and *planning* decision types of the decision-support taxonomy: the dashboard does not make decisions; it compresses 20.8 million records into comparable aggregate measures for human judgment [1][2].

---

## 2. Data Warehouse Context

### 2.1 Warehouse schema (as loaded by the ETL)

The fact grain is **one row per train stop per service**. The dashboard joins four normalized tables on the natural keys `service_rdt_id` and `stop_rdt_id`:

| Table | Key | Role | Columns used |
|---|---|---|---|
| `Warehouse` | `(service_rdt_id, stop_rdt_id)` | Facts (times, cancellations, departure delay) | `arrival_time`, `arrival_cancelled`, `departure_time`, `departure_delay`, `departure_cancelled` |
| `Location` | `(service_rdt_id, stop_rdt_id)` | Station context + arrival delay | `station_name`, `station_code`, `arrival_delay` |
| `Train` | `service_rdt_id` | Service dimension | `train_number`, `service_type`, `service_date`, `company` |
| `Stop` | `stop_rdt_id` | Station reference | `station_name`, `station_code`, `completely_cancelled`, `partly_cancelled` |

Because the ETL does not build a separate date dimension, **time attributes are derived in SQL** from the event clock time:

```sql
event_time = COALESCE(w.departure_time, w.arrival_time, TIMESTAMP(t.service_date))
```

with `YEAR`, `QUARTER`, `MONTH`, `DATE`, `HOUR`, `DAYNAME` and `WEEKDAY` computed on the fly. The **peak-hour flag** is likewise derived: weekday **and** hour in 07–09 or 16–18, mirroring the CBS morning/evening peak definitions used in Dutch transport statistics [8].

### 2.2 Dataset profile

| Property | Value |
|---|---|
| Coverage | 2019-01-01 → 2019-12-31 |
| Stop records (`Warehouse`) | 20,773,804 |
| Services (`Train`) | ≈ 2.4 million |
| Operating companies | 15 (NS, Arriva, Breng, Blauwnet, R-net, …) |
| Service types | Intercity, Sprinter, Stoptrein, ICE International, Extra trein, … |
| Join core | `Warehouse ⨝ Location ⨝ Train ⨝ Stop` |

*Table 1. Warehouse profile.*

---

## 3. Analytical Reports

Each report is implemented as a fully **parameterized** SQL statement (values sent separately via `%s` placeholders through `mysql-connector-python`) — the industry-recommended defense against SQL injection [6]. The `<view SQL>` expander in the running application displays the exact statement sent to MySQL for every chart.

### Report 1 — Delay Trend Over Time

**Purpose.** Task 1: compare average delays (bars) against cancellation rates (lines, secondary axis) across a selectable time grain.

**SQL (as executed by the application; grain = Day):**

```sql
SELECT period,
       AVG(arrival_delay_minutes)      AS avg_arrival_delay,
       AVG(departure_delay_minutes)    AS avg_departure_delay,
       100.0 * AVG(arrival_cancelled)  AS arrival_cancel_rate,
       100.0 * AVG(departure_cancelled) AS departure_cancel_rate,
       COUNT(*)                        AS stop_count,
       MIN(y) AS y, MIN(q) AS q, MIN(m) AS m, MIN(d) AS d, MIN(h) AS h
FROM (
    SELECT DATE(et) AS period,                       -- grain expression varies: YEAR/QUARTER/MONTH/DATE/HOUR
           YEAR(et) AS y, QUARTER(et) AS q, MONTH(et) AS m,
           DATE(et) AS d, HOUR(et) AS h,
           arrival_delay_minutes, departure_delay_minutes,
           arrival_cancelled, departure_cancelled
    FROM (
        SELECT COALESCE(w.departure_time, w.arrival_time,
                        TIMESTAMP(t.service_date)) AS et,
               l.arrival_delay      AS arrival_delay_minutes,
               w.departure_delay    AS departure_delay_minutes,
               w.arrival_cancelled  AS arrival_cancelled,
               w.departure_cancelled AS departure_cancelled
        FROM Warehouse w
        JOIN Location l ON l.service_rdt_id = w.service_rdt_id AND l.stop_rdt_id = w.stop_rdt_id
        JOIN Train t    ON t.service_rdt_id = w.service_rdt_id
        JOIN Stop s     ON s.stop_rdt_id    = w.stop_rdt_id
        WHERE t.service_date BETWEEN %s AND %s        -- sidebar date slice
          AND t.company IN (%s, %s)                   -- sidebar company slice (dice)
          AND COALESCE(...) NOT IN (1,7) ...          -- peak-mode dice (when active)
    ) base
) levels
GROUP BY period
ORDER BY period;
```

**Advanced SQL constructs.**

| Construct | Purpose |
|---|---|
| Three-level nested subqueries | Separate row filtering (inner) → grain projection (middle) → aggregation (outer), keeping the grain expression swappable without touching the filter layer |
| `COALESCE(departure_time, arrival_time, TIMESTAMP(service_date))` | Robust event-time derivation: cancelled stops with missing timestamps fall back instead of vanishing from measures [3, ch. 2] |
| `100.0 * AVG(...)` rate metrics | Computes percentages as aggregate ratios in SQL instead of shipping row-level data to the client |
| `GROUP BY period` (alias) / `ORDER BY period` | MySQL alias grouping keeps the statement readable while the grain expression is injected per selection |
| `CONCAT`/`LPAD` | Human-friendly period labels (`2019-06`, `2019-Q2`, `2019-06-01 08:00`) |
| Parameterized predicates (`%s`) | Every sidebar value is bound, never interpolated [6] |

**OLAP operations.**

- **Roll-up / drill-down:** the grain selector swaps `YEAR(et)` → `QUARTER(et)` → `MONTH(et)` → `DATE(et)` → `HOUR(et)`; clicking a bar additionally pins that member (`YEAR(et)=2019 AND QUARTER(et)=2`) and descends one level — the classic OLAP drill path with breadcrumb navigation [1][2].
- **Slice:** date range on `service_date`.
- **Dice:** date range × companies × peak-mode × delay threshold simultaneously.

**Sample output** (June 2019, Day grain; executed in 35.9 s against the live warehouse — extract):

| period | avg_arrival_delay | avg_departure_delay | arrival_cancel_rate | departure_cancel_rate | stop_count |
|---|---|---|---|---|---|
| 2019-06-01 | 0.40 | 0.45 | 0.58 | 0.59 | 47,658 |
| 2019-06-05 | 0.90 | 0.95 | 3.39 | 3.31 | 59,214 |
| 2019-06-18 | 0.70 | 0.73 | 1.39 | 1.38 | 59,092 |
| **2019-06-24** | **2.16** | **2.14** | **6.36** | **6.18** | 59,104 |
| 2019-06-25 | 1.41 | 1.45 | 4.55 | 4.50 | 59,157 |
| 2019-06-30 | 0.47 | 0.50 | 0.66 | 0.65 | 43,180 |

*Table 2. Daily trend extract, June 2019 (n = 31 periods).*

The extract reveals a **network disruption on 24–25 June 2019**: average delays roughly triple and cancellations rise from ≈1% to ≈6% — precisely the pattern a duty controller would drill into (hour grain, station slice) during incident review.

> **Figure 1 (insert screenshot).** Delay Trend Over Time chart in the running app, showing grouped delay bars and dotted cancellation lines on the secondary axis. *Capture: `streamlit run STADVDB-MCO1-Group9-OLAP.py` → Report 1.*

---

### Report 2 — Cancellation Rates by Company

**Purpose.** Task 2: rank operating companies by arrival/departure cancellation percentages, drillable to service type and train number.

**SQL (Company level):**

```sql
SELECT t.company AS member,
       COUNT(*) AS stop_count,
       AVG(l.arrival_delay)   AS avg_arrival_delay,
       AVG(w.departure_delay) AS avg_departure_delay,
       100.0 * AVG(w.arrival_cancelled) AS arrival_cancel_rate,
       100.0 * AVG(w.departure_cancelled) AS departure_cancel_rate,
       100.0 * AVG(CASE WHEN w.arrival_cancelled = 1
                         OR w.departure_cancelled = 1
                        THEN 1 ELSE 0 END) AS any_cancel_rate
FROM Warehouse w
JOIN Location l ON l.service_rdt_id = w.service_rdt_id AND l.stop_rdt_id = w.stop_rdt_id
JOIN Train t    ON t.service_rdt_id = w.service_rdt_id
JOIN Stop s     ON s.stop_rdt_id    = w.stop_rdt_id
WHERE ...                                              -- shared slice/dice context
GROUP BY member                                        -- level expression swaps: t.company | t.service_type | CAST(t.train_number AS CHAR)
ORDER BY any_cancel_rate DESC, stop_count DESC;
```

**Advanced SQL constructs.**

| Construct | Purpose |
|---|---|
| **Conditional aggregation** `AVG(CASE WHEN … THEN 1 ELSE 0 END)` | Turns boolean cancellation flags into a percentage metric in one pass — the standard SQL idiom for ratio measures [4] |
| Hierarchy-swapped `GROUP BY` | One statement serves three hierarchy levels; the application substitutes the whitelisted level expression (`DIM_EXPR`), never user text |
| `CAST(t.train_number AS CHAR)` | Type harmonization so the numeric train number groups and joins as a display member |
| Multi-key `ORDER BY` | Ranks by cancellation rate while breaking ties by traffic volume (`stop_count`) to suppress small-sample noise |

**OLAP operations.**

- **Roll-up:** Train Number → Service Type → Company via the hierarchy selector.
- **Drill-down:** clicking a company bar pins `t.company = 'NS'` and descends to its service types; clicking again pins the service type and lists individual trains.
- **Slice/dice:** all sidebar filters compose (e.g., *Peak only* × June 2019 × selected stations).

**Sample output** (June 2019, Company level; executed in 39.5 s — extract):

| member | stop_count | avg_arr_delay | avg_dep_delay | arrival_cancel_% | departure_cancel_% | any_cancel_% |
|---|---|---|---|---|---|---|
| Railpromo | 105 | 1.10 | 0.87 | 11.43 | 6.67 | **15.24** |
| Eurobahn | 3,027 | 1.52 | 1.15 | 4.43 | 4.43 | **6.87** |
| Breng | 9,405 | 0.33 | 0.36 | 4.09 | 4.00 | **4.67** |
| R-net | 60,182 | 0.41 | 0.48 | 2.15 | 2.15 | **2.57** |
| NS | 1,157,298 | 0.75 | 0.78 | 1.71 | 1.68 | **2.11** |
| Arriva | 290,558 | 0.52 | 0.60 | 0.89 | 0.88 | **1.09** |
| GVB | 4,579 | NULL | 0.00 | 0.00 | 0.00 | **0.00** |

*Table 3. Cancellation rates by company, June 2019 (15 groups; extract of 7).*

Interpretation: Railpromo and Eurobahn show high cancellation rates on small volumes, while NS — 70 % of all stops — sits at 2.1 %. The multi-key ordering prevents tiny operators from visually dominating the chart. NULL average arrival delays indicate operators whose arrival times are not recorded in the source (e.g., terminating services), handled transparently by the dashboard rather than imputed.

> **Figure 2 (insert screenshot).** Cancellation Rates chart at Company level, mid-drill on NS showing its service types. *Capture: click the NS bar after running the app.*

---

### Report 3 — Delay Severity Ranking by Train

**Purpose.** Task 3: identify the worst-performing individual services (Top-N) for targeted operational follow-up.

**SQL (Top 10):**

```sql
SELECT t.train_number, t.company, t.service_type,
       COUNT(*) AS stop_count,
       AVG(l.arrival_delay)   AS avg_arrival_delay,
       AVG(w.departure_delay) AS avg_departure_delay,
       GREATEST(COALESCE(AVG(l.arrival_delay), 0),
                COALESCE(AVG(w.departure_delay), 0)) AS severity_score,
       100.0 * AVG(CASE WHEN w.arrival_cancelled = 1 OR w.departure_cancelled = 1
                        THEN 1 ELSE 0 END) AS cancel_rate
FROM Warehouse w
JOIN Location l ON ...
JOIN Train t ON ...
JOIN Stop s ON ...
WHERE ...
GROUP BY t.train_number, t.company, t.service_type
ORDER BY severity_score DESC, stop_count DESC
LIMIT %s;                          -- Top-N slider (10/15/25/50/100)
```

**Advanced SQL constructs.**

| Construct | Purpose |
|---|---|
| `GREATEST(COALESCE(AVG(...),0), COALESCE(AVG(...),0))` | Composite severity metric: the worse of the two delay dimensions; `COALESCE` guards against NULL arrival columns so a partially-recorded service still ranks [5] |
| `LIMIT %s` bound parameter | Top-N control is parameterized like every other filter |
| Multi-column `GROUP BY` | Keeps company and service type attached to each train so the analyst can see *who* operates the offender |

**OLAP operations.** Slice/dice from the sidebar fully applies; the report is the **leaf level** of the service hierarchy reached by drilling down Report 2 (Company → Service Type → Train Number) — demonstrating drill-across: the same member selected in Report 2 highlights the same trains here, because every query shares one filter context [2].

**Sample output** (June 2019, Top 10; executed in 41.7 s):

| train_number | company | service_type | stop_count | avg_arr_delay | avg_dep_delay | severity_score | cancel_% |
|---|---|---|---|---|---|---|---|
| 37483 | Arriva | Stoptrein | 2 | 227.00 | 0.00 | **227.00** | 0.00 |
| 200689 | NS | Intercity | 2 | 42.00 | 43.00 | **43.00** | 0.00 |
| 152 | NS | ICE International | 7 | 36.83 | 26.33 | **36.83** | 28.57 |
| 104 | NS | ICE International | 81 | 31.17 | 26.35 | **31.17** | 7.41 |
| 29295 | NS | Extra trein | 5 | 30.00 | 29.75 | **30.00** | **60.00** |
| 28789 | NS | Extra trein | 4 | 27.67 | 14.00 | **27.67** | 0.00 |
| 156 | NS | ICE International | 9 | 27.13 | 22.00 | **27.13** | 22.22 |

*Table 4. Top trains by delay severity, June 2019 (extract of 10).*

Note the **outlier at the top** (train 37483: 227 min on only 2 stops) — a data-quality artifact typical of open transport data. The application mitigates this analytically through the *stop_count* column shown alongside severity and the sidebar **delay-threshold slider**, which prunes extreme single-stop records before ranking — a practical robustness affordance discussed in data-quality literature [5].

> **Figure 3 (insert screenshot).** Severity ranking bar chart (horizontal bars, colored by average departure delay). *Capture: Report 3 in the running app.*

---

### Report 4 — Hourly Delay Heatmap (Day × Hour)

**Purpose.** Task 4: expose the temporal concentration of delays to validate peak-hour policies.

**SQL:**

```sql
SELECT day_name, day_of_week, hour_of_day,
       AVG(arrival_delay_minutes)   AS avg_arrival_delay,
       AVG(departure_delay_minutes) AS avg_departure_delay,
       100.0 * AVG(arrival_cancelled)  AS arrival_cancel_rate,
       100.0 * AVG(departure_cancelled) AS departure_cancel_rate,
       COUNT(*) AS stop_count
FROM (
    SELECT DAYNAME(COALESCE(w.departure_time, w.arrival_time,
                            TIMESTAMP(t.service_date))) AS day_name,
           WEEKDAY(COALESCE(...)) + 1  AS day_of_week,  -- 1 = Monday … 7 = Sunday
           HOUR(COALESCE(...))         AS hour_of_day,
           l.arrival_delay AS arrival_delay_minutes,
           w.departure_delay, w.arrival_cancelled, w.departure_cancelled
    FROM Warehouse w
    JOIN Location l ON ...
    JOIN Train t ON ...
    JOIN Stop s ON ...
    WHERE ...
) base
GROUP BY day_name, day_of_week, hour_of_day
ORDER BY day_of_week, hour_of_day;
```

**Advanced SQL constructs.**

| Construct | Purpose |
|---|---|
| `DAYNAME` / `WEEKDAY()+1` / `HOUR` extraction | Rebuilds the day×hour time cube from raw timestamps; ISO-style Monday-first ordering (`WEEKDAY` returns 0 for Monday) [8] |
| Derived-column `GROUP BY` on inner aliases | Aggregates ~1.6 M June rows into ≤168 cells before any data leaves the database |
| Post-processing pivot (pandas) | The flat result is pivoted client-side into the heat grid — keeping SQL simple while enabling metric switching without re-pivoting in SQL |

**OLAP operations.** Roll-up across time is implicit here (the grid is the finest hour grain, roll-ups available in Report 1); slicing applies identically — e.g., *Peak only* keeps only the 07–09 and 16–18 bands, a textbook **dice** on two derived dimension members (day type × hour band).

**Sample output** (June 2019, peak-band extract; executed in 35.0 s):

| day_name | hour | avg_departure_delay (min) | stop_count |
|---|---|---|---|
| Monday | 08 | 0.6 | 12,990 |
| Monday | 17 | **1.7** | 13,188 |
| Tuesday | 08 | 0.9 | 13,893 |
| Tuesday | 17 | 1.2 | 13,928 |
| Friday | 17 | 1.0 | 13,918 |
| Saturday | 08 | 0.5 | 13,605 |
| Sunday | 08 | **0.4** | 11,037 |

*Table 5. Morning (08:00) vs evening (17:00) average departure delay by weekday, June 2019.*

The pattern is operationally meaningful: the **evening peak is consistently worse than the morning peak** (Monday 17:00 ≈ 3× Monday 08:00), and weekends are calm — consistent with published Dutch punctuality patterns where delay accumulation builds through the operating day [7][8].

> **Figure 4 (insert screenshot).** Heatmap in the running app (metric selector set to *Avg departure delay*). *Capture: Report 4 in the running app.*

---

### KPI header (contextual summary)

All four reports sit under four always-visible KPI tiles computed with the same filter context:

```sql
SELECT COUNT(*) AS total_stops,
       AVG(l.arrival_delay)   AS avg_arrival_delay,
       AVG(w.departure_delay) AS avg_departure_delay,
       100.0 * AVG(CASE WHEN w.arrival_cancelled = 1 OR w.departure_cancelled = 1
                        THEN 1 ELSE 0 END) AS cancel_rate
FROM Warehouse w JOIN Location l ON ... JOIN Train t ON ... JOIN Stop s ON ...
WHERE ...;
```

*June 2019 full-slice result:* **1,643,539 stops · avg arrival 0.68 min · avg departure 0.72 min · cancellation rate 1.93 %** (executed in 38.4 s).

---

## 4. OLAP Operations — Summary Mapping

| OLAP operation [1] | User action in the UI | Implementation in SQL |
|---|---|---|
| **Slice** | Pick one date range, one station, one company | `WHERE` equality/range predicates on dimension attributes |
| **Dice** | Combine peak-mode × companies × date range × delay threshold | Conjunctive composition of multiple slice predicates (sub-cube) |
| **Roll-up** | Choose a coarser time grain or higher service level | Swaps the `GROUP BY` expression to the ancestor level (`HOUR` → `DAY` → `MONTH` → `QUARTER` → `YEAR`) |
| **Drill-down** | Click a chart member or pick a finer level | Appends the member's equality predicate **and** selects the child grain expression |
| **Drill-across** | Observe one slice simultaneously in all reports | All five queries share a single assembled WHERE clause (`build_where()`), guaranteeing cross-report consistency |

*Table 6. OLAP operation mapping.*

---

## 5. Engineering Decisions Supporting the Analytical Design

1. **Live SQL over pre-aggregation.** Every interaction compiles to SQL against the operational warehouse rather than a proprietary OLAP server. This follows the relational-OLAP (ROLAP) approach, where aggregation is expressed with standard `GROUP BY` over a dimensional schema [2][4]. It sacrifices the response times of MOLAP cubes but guarantees always-fresh results and full auditability via the *View SQL* expanders.
2. **Parameterized queries everywhere.** All user selections are bound as `%s` parameters; dimension expressions are drawn from hardcoded whitelists. This implements the OWASP SQL-injection prevention guidance [6] and keeps the analytical surface safe for multi-user deployment.
3. **Result caching (5-minute TTL).** `st.cache_data` memoizes each unique (SQL, parameters) pair, so toggling filters back and forth is served from memory — a lightweight client-side analogue of materialized-view reuse, whose benefits for repeated OLAP queries are well documented [5].
4. **Connection pooling.** A 20-connection `mysql-connector-python` pool with a 10 s connect timeout absorbs overlapping Streamlit reruns against a 20.8 M-row warehouse without exhausting connections.
5. **Derived time dimension.** Computing year/quarter/month/hour from `COALESCE(departure_time, arrival_time, service_date)` avoids restructuring the proven ETL pipeline while still enabling the full time hierarchy demanded by roll-up/drill-down — a pragmatic compromise described in dimensional-modeling practice for late-arriving or missing facts [3].

**Known limitation.** The MySQL instance runs with the default `innodb_buffer_pool_size` (128 MB) against ~4.2 GB of table data, so cold aggregations execute in tens of seconds. The report figures above were produced with real executions under exactly these conditions; raising the buffer pool (documented in the project README) or indexing `service_date` would shift the workload toward in-memory execution [4].

---

## 6. Figures Checklist

| Figure | Content | Where to capture |
|---|---|---|
| Figure 1 | Delay Trend Over Time (grouped bars + cancellation lines) | App → Report 1 |
| Figure 2 | Cancellations by Company, mid-drill breadcrumb visible | App → Report 2, click NS bar |
| Figure 3 | Delay Severity Ranking (horizontal Top-N bars) | App → Report 3 |
| Figure 4 | Hourly Delay Heatmap | App → Report 4 |
| (optional) Figure 0 | Full dashboard with sidebar filters visible | App, default view |

Run the app once (`.\.venv\Scripts\python.exe -m streamlit run STADVDB-MCO1-Group9-OLAP.py`), then capture the four screenshots above and drop them into this section of your submitted report.

---

## References

[1] E. F. Codd, S. C. Codd, and C. T. Salley, "Providing OLAP to User-Analysts," *OLAP Council*, 1993. (Origin of the term OLAP and the roll-up/drill-down/slice/dice vocabulary.)

[2] S. Chaudhuri and U. Dayal, "An Overview of Data Warehousing and OLAP Technology," *ACM SIGMOD Record*, vol. 26, no. 1, pp. 65–74, 1997. doi:10.1145/248603.248616.

[3] R. Kimball and M. Ross, *The Data Warehouse Toolkit: The Definitive Guide to Dimensional Modeling*, 3rd ed. Indianapolis, IN: Wiley, 2013.

[4] M. Golfarelli and S. Rizzi, *Data Warehouse Design: Modern Principles and Applications*. New York: Springer, 2015.

[5] A. Gupta and I. S. Mumick, "Maintenance of Materialized Views: Problems, Techniques, and Applications," *IEEE Data Engineering Bulletin*, vol. 18, no. 2, pp. 3–18, 1995. (Supports the memoization/materialization strategy for repeated OLAP queries.)

[6] Open Web Application Security Project (OWASP), *SQL Injection Prevention Cheat Sheet*, 2024. Available: https://cheatsheetseries.owasp.org/cheatsheets/SQL_Injection_Prevention_Cheat_Sheet.html

[7] Nederlandse Spoorwegen / ProRail, *Punctualiteitsrapportages* (punctuality reporting methodology: share of trains arriving within 5 and 15 minutes of timetable). Available: https://www.prorail.nl

[8] Centraal Bureau voor de Statistiek (CBS), "Punctualiteit treinvervoer," *StatLine*, 2024. (Definitions of morning/evening peak windows and national punctuality benchmarks.)

[9] Streamlit Documentation, *st.cache_data & Performance*, 2025. Available: https://docs.streamlit.io

[10] Oracle, *mysql-connector-python Developer Guide — Connection Pooling*, 2024. Available: https://dev.mysql.com/doc/connector-python/en/
