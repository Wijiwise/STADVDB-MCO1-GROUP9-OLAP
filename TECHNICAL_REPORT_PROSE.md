# Technical Report — Prose Version (for rewriting in your own words)

*Companion to `TECHNICAL_REPORT.md`. Same content, written as flowing paragraphs so it can be paraphrased easily. SQL statements are kept verbatim (they must not be reworded); all numbers below are real outputs executed against the warehouse.*

---

## 1. Purpose of the Application

The DutchRail OLAP Web Dashboard is an interactive decision-support application that allows rail operations analysts to explore a data warehouse containing 20.8 million historical train-stop records from the Dutch railway network. Rather than requiring users to write SQL, the application translates the classic OLAP operations — slice, dice, roll-up and drill-down — into parameterized queries that are executed live against a MySQL database and visualized with interactive charts. The motivation for building such an application follows the fundamental goal of data warehousing described by Chaudhuri and Dayal (1997): consolidating operational data into a single, consistent analytical layer so that decision makers can examine historical performance from several perspectives without altering the source data. An ETL pipeline loads and cleans raw NS open-data extracts into a normalized warehouse called `railway_dw`, and this dashboard serves as the analytical front end over that curated layer, in line with the dimensional modelling principles advocated by Kimball and Ross (2013).

## 2. Decision-Making and Analytical Tasks

The application is intended for four analytical tasks that correspond to real decisions in railway operations management. The first task is punctuality monitoring over time: by observing how average delays and cancellation rates evolve at different time granularities, managers can evaluate the impact of disruption days and decide when to schedule maintenance, staffing and timetable recovery buffers. The second task is comparing operating companies, because the Dutch network is run by several concession holders such as NS, Arriva and Breng that are contractually obliged to meet punctuality standards; ranking them on identical filters provides the evidence base for contract monitoring and penalty discussions. The third task is service-level diagnosis: company averages can hide individual problem trains, so ranking specific train numbers by delay severity allows planners to target particular timetables or rolling stock for intervention. The fourth task is peak-hour profiling, which reveals when during the day and week delays concentrate and therefore informs decisions about surge staffing, track-work windows and capacity planning. In the decision-support taxonomy, these tasks cover monitoring, control and planning functions: the application itself does not decide anything, but it compresses millions of records into comparable aggregate measures that support human judgment.

## 3. Data Warehouse Context

The warehouse has a fact grain of one row per train stop per service, distributed across four normalized tables. The `Warehouse` table stores the event facts — arrival and departure times, departure delay and the arrival/departure cancellation flags — while `Location` carries the station context and arrival delay, `Train` carries the service attributes such as train number, service type, company and service date, and `Stop` carries station reference data and whole-service cancellation flags. The dashboard joins these tables on the natural keys `service_rdt_id` and `stop_rdt_id`. Because the existing ETL pipeline, which the team has already validated, does not build a separate calendar dimension, time attributes are derived directly in SQL from an event clock time computed as the departure time when available, otherwise the arrival time, and finally the service date at midnight as a last resort. Year, quarter, month, day, hour, day name and weekday number are all extracted from this expression on the fly, and a peak-hour flag is derived as weekdays between 07:00–09:00 and 16:00–18:00, mirroring the morning and evening peak windows used in Dutch transport statistics published by CBS. The dataset covers the full calendar year 2019, contains 20,773,804 stop records, roughly 2.4 million services, fifteen operating companies and service types including Intercity, Sprinter, Stoptrein and ICE International.

## 4. Report 1 — Delay Trend Over Time

The first report answers the question of when railway performance deteriorated. It displays average arrival and departure delays as grouped bars and arrival and departure cancellation rates as lines on a secondary axis, aggregated at a time grain chosen by the user. The application composes the query as a three-level nested statement: the innermost subquery joins the four tables and applies every sidebar filter; the middle subquery projects the period label for the selected grain along with the raw components needed for click-based drilling; and the outermost subquery aggregates the measures per period. The SQL below shows the Day-grain variant, with comments indicating where the grain expression and filters vary.

```sql
SELECT period,
       AVG(arrival_delay_minutes)       AS avg_arrival_delay,
       AVG(departure_delay_minutes)     AS avg_departure_delay,
       100.0 * AVG(arrival_cancelled)   AS arrival_cancel_rate,
       100.0 * AVG(departure_cancelled) AS departure_cancel_rate,
       COUNT(*)                         AS stop_count,
       MIN(y) AS y, MIN(q) AS q, MIN(m) AS m, MIN(d) AS d, MIN(h) AS h
FROM (
    SELECT DATE(et) AS period,
           YEAR(et) AS y, QUARTER(et) AS q, MONTH(et) AS m,
           DATE(et) AS d, HOUR(et) AS h,
           arrival_delay_minutes, departure_delay_minutes,
           arrival_cancelled, departure_cancelled
    FROM (
        SELECT COALESCE(w.departure_time, w.arrival_time,
                        TIMESTAMP(t.service_date)) AS et,
               l.arrival_delay       AS arrival_delay_minutes,
               w.departure_delay     AS departure_delay_minutes,
               w.arrival_cancelled   AS arrival_cancelled,
               w.departure_cancelled AS departure_cancelled
        FROM Warehouse w
        JOIN Location l ON l.service_rdt_id = w.service_rdt_id AND l.stop_rdt_id = w.stop_rdt_id
        JOIN Train t    ON t.service_rdt_id = w.service_rdt_id
        JOIN Stop s     ON s.stop_rdt_id    = w.stop_rdt_id
        WHERE t.service_date BETWEEN %s AND %s
    ) base
) levels
GROUP BY period
ORDER BY period;
```

Several advanced SQL constructs appear in this statement. The three-level subquery nesting separates row filtering from grain projection from aggregation, which means the time grain can be swapped without rewriting any filter logic. The COALESCE expression guarantees that stops with missing timestamps — typically cancelled services — still contribute to the measures instead of silently disappearing, addressing the late-arriving and incomplete fact problem discussed in dimensional modelling practice. Rates are computed in the database as `100.0 * AVG(...)` so that only compact aggregates cross the network rather than row-level data, and period labels are formatted with CONCAT and LPAD so that outputs such as 2019-Q2 or 2019-06-01 08:00 are directly readable on the chart axis. Every user-supplied value is bound through `%s` placeholders rather than concatenated into the statement, which follows the OWASP guidance for preventing SQL injection.

In OLAP terms, this report implements roll-up and drill-down by exchanging the grouping expression — HOUR rolls up to DAY, then MONTH, QUARTER and YEAR — while a click on any bar pins that member as an equality predicate, for example year equals 2019 and quarter equals 2, and descends one level deeper; the breadcrumb in the interface shows the current drill path. The sidebar date range provides slicing, and combining it with company selections, peak mode and the delay threshold produces a diced sub-cube. Executed against the live warehouse for June 2019 at day grain, the query returned 31 periods and completed in 35.9 seconds. The output shows that average delays normally sat between 0.4 and 0.9 minutes with cancellation rates around one percent, but on 24 June 2019 average arrival delay jumped to 2.16 minutes and cancellations to 6.36 percent, with 25 June remaining elevated at 1.41 minutes and 4.55 percent — a clear two-day network disruption that a duty controller could immediately drill into at hour level. *[Insert Figure 1: screenshot of Report 1 from the running application.]*

## 5. Report 2 — Cancellation Rates by Company

The second report supports operator comparison by showing arrival and departure cancellation percentages for each operating company, with the ability to drill through the hierarchy from company to service type to individual train number. The query groups by a level expression that the application substitutes from a hardcoded whitelist — company, service type, or cast train number — so a single statement serves all three hierarchy levels:

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
WHERE ...
GROUP BY member
ORDER BY any_cancel_rate DESC, stop_count DESC;
```

The central construct here is conditional aggregation: the expression `100.0 * AVG(CASE WHEN ... THEN 1 ELSE 0 END)` converts binary cancellation flags into a percentage within a single pass over the data, which is the standard SQL idiom for ratio measures. Casting the train number to a character type harmonizes it with the display members of the other hierarchy levels, and the multi-key ordering sorts primarily by cancellation rate while breaking ties by stop count so that operators with very little traffic do not visually dominate the ranking. For OLAP operations, the hierarchy selector performs roll-up and drill-down, clicking a company bar pins that member and descends to its service types, and all sidebar filters apply conjunctively to form a dice. The June 2019 execution, which took 39.5 seconds, returned fifteen companies. Railpromo showed the highest cancellation rate at 15.24 percent but on only 105 stops, Eurobahn followed at 6.87 percent across 3,027 stops, and Breng at 4.67 percent across 9,405 stops, while NS — which accounts for 1,157,298 of the month's stops — recorded 2.11 percent and Arriva 1.09 percent on 290,558 stops. Several smaller operators returned a NULL average arrival delay because arrival times are not recorded for their terminating services, and the dashboard reports this transparently instead of imputing values. The practical reading is that high percentages among tiny operators must be weighed against stop counts, which is exactly why both columns are displayed together. *[Insert Figure 2: screenshot of Report 2 mid-drill on a company.]*

## 6. Report 3 — Delay Severity Ranking by Train

The third report drills to the leaf of the service hierarchy and ranks individual train services by a severity score, allowing planners to identify the worst offenders behind the company-level averages. Its distinguishing construct is the use of GREATEST combined with COALESCE to build a composite metric from aggregate values:

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
JOIN Location l ON l.service_rdt_id = w.service_rdt_id AND l.stop_rdt_id = w.stop_rdt_id
JOIN Train t    ON t.service_rdt_id = w.service_rdt_id
JOIN Stop s     ON s.stop_rdt_id    = w.stop_rdt_id
WHERE ...
GROUP BY t.train_number, t.company, t.service_type
ORDER BY severity_score DESC, stop_count DESC
LIMIT %s;
```

The severity score is the worse of the two average delay dimensions, and the COALESCE wrappers ensure that a service missing one of the delay columns still receives a fair score instead of falling out of the result. The Top-N slider value is bound as a parameter just like every sidebar filter, and grouping by train number together with company and service type keeps the operator and category attached to each ranked row. From an OLAP perspective, this report is the service hierarchy's leaf level and demonstrates drill-across: because every query in the dashboard shares one assembled WHERE clause, a member selected in Report 2 appears consistently here. The June 2019 execution ran in 41.7 seconds and returned, among others, train 152 (ICE International) with 36.83 minutes average arrival delay and a 28.57 percent cancellation rate over seven stops, train 104 (ICE International) with 31.17 minutes over 81 stops, and train 29295 (an NS Extra trein) with a 60 percent cancellation rate over five stops. The top row, train 37483 at 227 minutes on only two stops, is a data-quality artifact of the kind common in open transport data; the interface mitigates this by always showing stop counts next to the score and by offering a delay-threshold slider in the sidebar that prunes extreme single-stop records before ranking. *[Insert Figure 3: screenshot of the severity ranking chart.]*

## 7. Report 4 — Hourly Delay Heatmap

The fourth report exposes the intra-day pattern of delays by building a day-of-week by hour-of-day grid with a selectable metric. The SQL extracts the temporal components from the derived event time and aggregates roughly 1.6 million June rows into at most 168 cells before any data leaves the database:

```sql
SELECT day_name, day_of_week, hour_of_day,
       AVG(arrival_delay_minutes)       AS avg_arrival_delay,
       AVG(departure_delay_minutes)     AS avg_departure_delay,
       100.0 * AVG(arrival_cancelled)   AS arrival_cancel_rate,
       100.0 * AVG(departure_cancelled) AS departure_cancel_rate,
       COUNT(*) AS stop_count
FROM (
    SELECT DAYNAME(COALESCE(w.departure_time, w.arrival_time,
                            TIMESTAMP(t.service_date))) AS day_name,
           WEEKDAY(COALESCE(w.departure_time, w.arrival_time,
                            TIMESTAMP(t.service_date))) + 1 AS day_of_week,
           HOUR(COALESCE(w.departure_time, w.arrival_time,
                         TIMESTAMP(t.service_date))) AS hour_of_day,
           l.arrival_delay AS arrival_delay_minutes,
           w.departure_delay, w.arrival_cancelled, w.departure_cancelled
    FROM Warehouse w
    JOIN Location l ON l.service_rdt_id = w.service_rdt_id AND l.stop_rdt_id = w.stop_rdt_id
    JOIN Train t    ON t.service_rdt_id = w.service_rdt_id
    JOIN Stop s     ON s.stop_rdt_id    = w.stop_rdt_id
    WHERE ...
) base
GROUP BY day_name, day_of_week, hour_of_day
ORDER BY day_of_week, hour_of_day;
```

DAYNAME, WEEKDAY and HOUR rebuild the time cube from raw timestamps, and adding one to WEEKDAY yields Monday-first numbering that matches how the results are ordered. The flat result set is pivoted client-side into the heat grid, which keeps the SQL simple while allowing the metric to be switched without re-aggregating. In OLAP terms this view is the finest hour-level grain of the time dimension; the peak-mode filter acts as a textbook dice on two derived members at once, restricting the grid to weekday rush hours. The June 2019 output illustrates a consistent operational pattern: the evening peak is systematically worse than the morning peak — Monday at 17:00 averaged 1.7 minutes of departure delay against 0.6 minutes at 08:00 — weekday values sit near 0.9 to 1.2 minutes in the evening band, and weekend mornings are the calmest at around 0.4 to 0.5 minutes. This accumulation of delay through the operating day agrees with the punctuality patterns reported by ProRail and CBS, and it justifies treating the evening rush as the priority window for staffing and recovery measures. *[Insert Figure 4: screenshot of the heatmap.]*

## 8. OLAP Operations in Summary

Across all four reports the OLAP vocabulary maps onto concrete SQL mechanisms. Slicing is the act of fixing one or more dimension members and is implemented as WHERE predicates on station, company, service type, train number or date. Dicing is the simultaneous restriction on several dimensions and is produced simply by composing those predicates conjunctively, for example peak hours combined with a company subset, a date range and a delay threshold. Roll-up moves up a hierarchy and is realised by swapping the GROUP BY expression to the parent level, whether in time (hour to day to month to quarter to year) or in the service hierarchy (train number to service type to company). Drill-down moves in the opposite direction, adding both the clicked member's equality predicate and the finer grouping expression, while breadcrumbs keep the analyst's position in the cube explicit. Drill-across is demonstrated by the fact that the KPI header and all four reports share one filter context, so a selection made anywhere is reflected consistently in every measure area, allowing several measures to be compared over the same dimension members — the practical benefit of a warehouse over isolated reports noted by Chaudhuri and Dayal.

## 9. Engineering Decisions and Limitations

Three engineering decisions deserve mention because they affect analytical quality. First, the application follows the relational OLAP approach: instead of loading results into a proprietary cube, every interaction compiles to standard GROUP BY SQL executed against the warehouse, which sacrifices some response speed but guarantees fresh results and full auditability through the View SQL panel. Second, all user selections are bound as parameters and dimension expressions are drawn from hardcoded whitelists, implementing the OWASP SQL-injection prevention guidance. Third, query results are memoized for five minutes keyed on the exact statement and parameter set, so toggling between filter combinations is served from memory — a lightweight client-side equivalent of the materialized-view reuse strategy described by Gupta and Mumick. The application also relies on a twenty-connection MySQL pool so that overlapping Streamlit reruns do not exhaust connections while slow queries are still running. The main limitation is that the MySQL instance still uses the default 128 MB buffer pool against roughly 4.2 GB of table data, so cold aggregations over the 20.8 million-row fact table execute in tens of seconds; all sample outputs quoted in this report were produced under exactly these conditions, and raising the buffer pool or indexing the service date column, both documented in the project README, would move the workload largely into memory.

## References

Chaudhuri, S., and Dayal, U. (1997). An Overview of Data Warehousing and OLAP Technology. *ACM SIGMOD Record*, 26(1), 65–74.

Codd, E. F., Codd, S. C., and Salley, C. T. (1993). Providing OLAP to User-Analysts. *OLAP Council*.

Golfarelli, M., and Rizzi, S. (2015). *Data Warehouse Design: Modern Principles and Applications*. Springer.

Gupta, A., and Mumick, I. S. (1995). Maintenance of Materialized Views: Problems, Techniques, and Applications. *IEEE Data Engineering Bulletin*, 18(2), 3–18.

Kimball, R., and Ross, M. (2013). *The Data Warehouse Toolkit: The Definitive Guide to Dimensional Modeling* (3rd ed.). Wiley.

Open Web Application Security Project (OWASP) (2024). *SQL Injection Prevention Cheat Sheet*.

Centraal Bureau voor de Statistiek (CBS) (2024). Punctualiteit treinvervoer. *StatLine*.

ProRail. *Punctualiteitsrapportages* (punctuality reporting methodology).

Streamlit Documentation (2025). *st.cache_data and performance*. https://docs.streamlit.io

Oracle (2024). *mysql-connector-python Developer Guide — Connection Pooling*.
