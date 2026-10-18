import datetime as dt

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import db

st.set_page_config(
    page_title="DutchRail OLAP Dashboard",
    page_icon=":train:",
    layout="wide",
)

TIME_GRAINS = ["Year", "Quarter", "Month", "Day", "Hour"]
TIME_INDEX = {name: i for i, name in enumerate(TIME_GRAINS)}

EVENT_TIME = "COALESCE(w.departure_time, w.arrival_time, TIMESTAMP(t.service_date))"
EVENT_DATE = f"DATE({EVENT_TIME})"

TIME_EXPR = {
    "Year": "YEAR(et)",
    "Quarter": "CONCAT(YEAR(et), '-Q', QUARTER(et))",
    "Month": "CONCAT(YEAR(et), '-', LPAD(MONTH(et), 2, '0'))",
    "Day": "DATE(et)",
    "Hour": "CONCAT(DATE(et), ' ', LPAD(HOUR(et), 2, '0'), ':00')",
}

PEAK_EXPR = (
    f"(CASE WHEN DAYOFWEEK({EVENT_TIME}) NOT IN (1, 7) "
    f"AND (HOUR({EVENT_TIME}) BETWEEN 7 AND 9 OR HOUR({EVENT_TIME}) BETWEEN 16 AND 18) "
    f"THEN 1 ELSE 0 END)"
)

DIM_LEVELS = ["Company", "Service Type", "Train Number"]
DIM_INDEX = {name: i for i, name in enumerate(DIM_LEVELS)}
DIM_EXPR = {
    "Company": "t.company",
    "Service Type": "t.service_type",
    "Train Number": "CAST(t.train_number AS CHAR)",
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


def _placeholders(count):
    return ", ".join(["%s"] * count)


def _norm_day(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()[:10]
    return str(value)[:10]


def _point_value(point, key, default=None):
    try:
        value = point[key]
    except Exception:
        value = getattr(point, key, default)
    return default if value is None else value


def _selection_points(chart_key):
    state = st.session_state.get(chart_key)
    if not state:
        return []
    if isinstance(state, dict):
        return list((state.get("selection") or {}).get("points") or [])
    selection = getattr(state, "selection", None)
    return list(getattr(selection, "points", None) or [])


def init_state():
    defaults = {
        "time_drill": [],
        "dim_drill": [],
        "time_grain": "Month",
        "dim_level": "Company",
        "last_time_token": None,
        "last_dim_token": None,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def consume_time_selection():
    points = _selection_points("trend_chart")
    if not points:
        return False
    grain = st.session_state.get("time_grain", "Year")
    depth = TIME_INDEX[grain]
    if depth >= len(TIME_GRAINS) - 1:
        return False
    point = points[0]
    custom = _point_value(point, "customdata")
    if not custom or len(custom) < 5:
        return False
    year, quarter, month, day, hour = custom
    token = f"trend|{grain}|{year}|{quarter}|{month}|{day}|{hour}"
    if st.session_state.get("last_time_token") == token:
        return False
    entry = {"grain": grain}
    if grain == "Year":
        entry["year"] = int(year)
    elif grain == "Quarter":
        entry["year"] = int(year)
        entry["quarter"] = int(quarter)
    elif grain == "Month":
        entry["year"] = int(year)
        entry["month"] = int(month)
    elif grain == "Day":
        entry["day"] = _norm_day(day)
    elif grain == "Hour":
        entry["day"] = _norm_day(day)
        entry["hour"] = int(hour)
    st.session_state["time_drill"] = st.session_state["time_drill"][:depth] + [entry]
    st.session_state["time_grain"] = TIME_GRAINS[depth + 1]
    st.session_state["last_time_token"] = token
    return True


def consume_dim_selection():
    points = _selection_points("dim_chart")
    if not points:
        return False
    level = st.session_state.get("dim_level", "Company")
    depth = DIM_INDEX[level]
    if depth >= len(DIM_LEVELS) - 1:
        return False
    member = _point_value(points[0], "x")
    if member is None:
        return False
    member = str(member)
    token = f"dim|{level}|{member}"
    if st.session_state.get("last_dim_token") == token:
        return False
    entry = {"level": level, "value": member}
    st.session_state["dim_drill"] = st.session_state["dim_drill"][:depth] + [entry]
    st.session_state["dim_level"] = DIM_LEVELS[depth + 1]
    st.session_state["last_dim_token"] = token
    return True


def on_time_grain_change():
    new_depth = TIME_INDEX[st.session_state["time_grain"]]
    st.session_state["time_drill"] = st.session_state["time_drill"][:new_depth]
    st.session_state["last_time_token"] = None


def on_dim_level_change():
    new_depth = DIM_INDEX[st.session_state["dim_level"]]
    st.session_state["dim_drill"] = st.session_state["dim_drill"][:new_depth]
    st.session_state["last_dim_token"] = None


def roll_up_time():
    if st.session_state["time_drill"]:
        st.session_state["time_drill"].pop()
        st.session_state["time_grain"] = TIME_GRAINS[len(st.session_state["time_drill"])]
        st.session_state["last_time_token"] = None


def roll_up_dim():
    if st.session_state["dim_drill"]:
        st.session_state["dim_drill"].pop()
        st.session_state["dim_level"] = DIM_LEVELS[len(st.session_state["dim_drill"])]
        st.session_state["last_dim_token"] = None


def reset_drills():
    st.session_state["time_drill"] = []
    st.session_state["dim_drill"] = []
    st.session_state["time_grain"] = "Month"
    st.session_state["dim_level"] = "Company"
    st.session_state["last_time_token"] = None
    st.session_state["last_dim_token"] = None


@st.cache_data(ttl=600)
def load_date_bounds():
    frame = db.run_query(
        "SELECT MIN(service_date) AS min_d, MAX(service_date) AS max_d FROM Train"
    )
    if frame.empty or pd.isna(frame.iloc[0]["min_d"]):
        today = dt.date.today()
        return today - dt.timedelta(days=30), today
    return frame.iloc[0]["min_d"], frame.iloc[0]["max_d"]


@st.cache_data(ttl=600)
def load_members(sql):
    return db.run_query(sql).iloc[:, 0].tolist()


def dimension_options():
    return {
        "stations": load_members(
            "SELECT DISTINCT station_name FROM Location ORDER BY station_name"
        ),
        "companies": load_members(
            "SELECT DISTINCT company FROM Train ORDER BY company"
        ),
        "service_types": load_members(
            "SELECT DISTINCT service_type FROM Train ORDER BY service_type"
        ),
        "train_numbers": load_members(
            "SELECT DISTINCT train_number FROM Train ORDER BY train_number"
        ),
    }


def render_sidebar(options, min_date, max_date):
    st.sidebar.header("Slice & Dice Filters")
    date_selection = st.sidebar.date_input(
        "Service date range",
        value=(min_date, max_date),
        min_value=min_date,
        max_value=max_date,
        help=(
            "Slices on Train.service_date. Charts aggregate each stop's event clock time "
            "(departure, else arrival, else service date)."
        ),
    )
    start_date = end_date = None
    if isinstance(date_selection, tuple):
        if len(date_selection) == 2:
            start_date, end_date = date_selection
        elif len(date_selection) == 1:
            start_date = end_date = date_selection[0]
    else:
        start_date = end_date = date_selection

    stations = st.sidebar.multiselect(
        "Stations",
        options["stations"],
        help="Slice by Location.station_name",
    )
    companies = st.sidebar.multiselect(
        "Operating companies", options["companies"], help="Slice by Train.company"
    )
    service_types = st.sidebar.multiselect(
        "Service types", options["service_types"], help="Slice by Train.service_type"
    )
    train_numbers = st.sidebar.multiselect(
        "Train numbers", options["train_numbers"], help="Slice by Train.train_number"
    )
    peak_mode = st.sidebar.radio(
        "Hour type",
        ["All hours", "Peak only", "Off-peak only"],
        help=(
            "Derived flag: peak = weekday 07:00-09:59 and 16:00-18:59 "
            "on the stop's event clock time (departure, else arrival)."
        ),
    )
    delay_min = st.sidebar.slider(
        "Minimum delay threshold (minutes)",
        min_value=0,
        max_value=120,
        value=0,
        help="Keeps stops where max(arrival, departure) delay >= threshold. 0 disables.",
    )
    st.sidebar.divider()
    st.sidebar.button("Reset drill-downs", on_click=reset_drills, use_container_width=True)
    if st.sidebar.button("Test database connection", use_container_width=True):
        try:
            version = db.test_connection()
            st.sidebar.success(f"Connected — MySQL {version}")
        except Exception as exc:
            db.reset_pool()
            st.sidebar.error(f"Connection failed: {exc}")

    return {
        "start": start_date,
        "end": end_date,
        "stations": stations,
        "companies": companies,
        "service_types": service_types,
        "train_numbers": train_numbers,
        "peak_mode": peak_mode,
        "delay_min": delay_min,
    }


def time_drill_sql(entries):
    clauses, params = [], []
    for entry in entries:
        grain = entry["grain"]
        if grain == "Year":
            clauses.append(f"YEAR({EVENT_TIME}) = %s")
            params.append(entry["year"])
        elif grain == "Quarter":
            clauses.append(f"YEAR({EVENT_TIME}) = %s AND QUARTER({EVENT_TIME}) = %s")
            params.extend([entry["year"], entry["quarter"]])
        elif grain == "Month":
            clauses.append(f"YEAR({EVENT_TIME}) = %s AND MONTH({EVENT_TIME}) = %s")
            params.extend([entry["year"], entry["month"]])
        elif grain == "Day":
            clauses.append(f"{EVENT_DATE} = %s")
            params.append(entry["day"])
        elif grain == "Hour":
            clauses.append(f"{EVENT_DATE} = %s AND HOUR({EVENT_TIME}) = %s")
            params.extend([entry["day"], entry["hour"]])
    return clauses, params


def dim_drill_sql(entries):
    clauses, params = [], []
    for entry in entries:
        clauses.append(f"{DIM_EXPR[entry['level']]} = %s")
        params.append(entry["value"])
    return clauses, params


def build_where(filters):
    clauses, params = [], []
    if filters["stations"]:
        clauses.append(f"l.station_name IN ({_placeholders(len(filters['stations']))})")
        params.extend(filters["stations"])
    if filters["companies"]:
        clauses.append(f"t.company IN ({_placeholders(len(filters['companies']))})")
        params.extend(filters["companies"])
    if filters["service_types"]:
        clauses.append(f"t.service_type IN ({_placeholders(len(filters['service_types']))})")
        params.extend(filters["service_types"])
    if filters["train_numbers"]:
        clauses.append(f"t.train_number IN ({_placeholders(len(filters['train_numbers']))})")
        params.extend(filters["train_numbers"])
    if filters["start"]:
        clauses.append("t.service_date >= %s")
        params.append(filters["start"].isoformat())
    if filters["end"]:
        clauses.append("t.service_date <= %s")
        params.append(filters["end"].isoformat())
    if filters["peak_mode"] == "Peak only":
        clauses.append(f"{PEAK_EXPR} = 1")
    elif filters["peak_mode"] == "Off-peak only":
        clauses.append(f"{PEAK_EXPR} = 0")
    if filters["delay_min"] > 0:
        clauses.append(
            "GREATEST(COALESCE(l.arrival_delay, 0), "
            "COALESCE(w.departure_delay, 0)) >= %s"
        )
        params.append(filters["delay_min"])
    time_clauses, time_params = time_drill_sql(st.session_state["time_drill"])
    dim_clauses, dim_params = dim_drill_sql(st.session_state["dim_drill"])
    clauses.extend(time_clauses)
    params.extend(time_params)
    clauses.extend(dim_clauses)
    params.extend(dim_params)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


def safe_query(sql, params):
    try:
        return db.run_query(sql, params)
    except Exception as exc:
        st.error(f"Query failed: {exc}")
        st.code(sql, language="sql")
        return pd.DataFrame()


def fetch_kpis(where, params):
    sql = f"""
SELECT COUNT(*) AS total_stops,
       AVG(l.arrival_delay) AS avg_arrival_delay,
       AVG(w.departure_delay) AS avg_departure_delay,
       {CANCEL_EXPR} AS cancel_rate
{BASE_FROM}
{where}
""".strip()
    return safe_query(sql, params), sql


def fetch_trend(grain, where, params):
    sql = f"""
SELECT period,
       AVG(arrival_delay_minutes) AS avg_arrival_delay,
       AVG(departure_delay_minutes) AS avg_departure_delay,
       100.0 * AVG(arrival_cancelled) AS arrival_cancel_rate,
       100.0 * AVG(departure_cancelled) AS departure_cancel_rate,
       COUNT(*) AS stop_count,
       MIN(y) AS y, MIN(q) AS q, MIN(m) AS m, MIN(d) AS d, MIN(h) AS h
FROM (
    SELECT {TIME_EXPR[grain]} AS period,
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
        {where}
    ) base
) levels
GROUP BY period
ORDER BY period
""".strip()
    return safe_query(sql, params), sql


def fetch_cancellations(where, params):
    level_expr = DIM_EXPR[st.session_state["dim_level"]]
    sql = f"""
SELECT {level_expr} AS member,
       COUNT(*) AS stop_count,
       AVG(l.arrival_delay) AS avg_arrival_delay,
       AVG(w.departure_delay) AS avg_departure_delay,
       100.0 * AVG(w.arrival_cancelled) AS arrival_cancel_rate,
       100.0 * AVG(w.departure_cancelled) AS departure_cancel_rate,
       {CANCEL_EXPR} AS any_cancel_rate
{BASE_FROM}
{where}
GROUP BY member
ORDER BY any_cancel_rate DESC, stop_count DESC
""".strip()
    return safe_query(sql, params), sql


def fetch_ranking(where, params, top_n):
    sql = f"""
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
{where}
GROUP BY t.train_number, t.company, t.service_type
ORDER BY severity_score DESC, stop_count DESC
LIMIT %s
""".strip()
    return safe_query(sql, params + [top_n]), sql


def fetch_heatmap(where, params):
    sql = f"""
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
    {where}
) base
GROUP BY day_name, day_of_week, hour_of_day
ORDER BY day_of_week, hour_of_day
""".strip()
    return safe_query(sql, params), sql


def fmt_time_entry(entry):
    grain = entry["grain"]
    if grain == "Year":
        return str(entry["year"])
    if grain == "Quarter":
        return f"{entry['year']} Q{entry['quarter']}"
    if grain == "Month":
        return f"{entry['year']}-{entry['month']:02d}"
    if grain == "Day":
        return str(entry["day"])
    return f"{entry['day']} {entry['hour']:02d}:00"


def fmt_dim_entry(entry):
    return f"{entry['level']}: {entry['value']}"


def active_context(filters):
    parts = []
    if filters["start"] or filters["end"]:
        parts.append(f"Dates: {filters['start']} to {filters['end']}")
    if filters["stations"]:
        shown = ", ".join(filters["stations"][:5])
        suffix = "..." if len(filters["stations"]) > 5 else ""
        parts.append(f"Stations: {shown}{suffix}")
    if filters["companies"]:
        parts.append(f"Companies: {', '.join(filters['companies'])}")
    if filters["service_types"]:
        parts.append(f"Service types: {', '.join(filters['service_types'])}")
    if filters["train_numbers"]:
        shown = ", ".join(map(str, filters["train_numbers"][:8]))
        suffix = "..." if len(filters["train_numbers"]) > 8 else ""
        parts.append(f"Trains: {shown}{suffix}")
    if filters["peak_mode"] != "All hours":
        parts.append(filters["peak_mode"])
    if filters["delay_min"] > 0:
        parts.append(f"Delay >= {filters['delay_min']} min")
    for entry in st.session_state["time_drill"]:
        parts.append(f"Drill: {fmt_time_entry(entry)}")
    for entry in st.session_state["dim_drill"]:
        parts.append(f"Drill: {entry['level']} = {entry['value']}")
    return " | ".join(parts) if parts else "No filters - full data cube"


def render_kpi_cards(kpis):
    if kpis.empty:
        return
    row = kpis.iloc[0]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Stops analyzed", f"{int(row['total_stops']):,}")
    c2.metric("Avg arrival delay", f"{float(row['avg_arrival_delay'] or 0):.1f} min")
    c3.metric("Avg departure delay", f"{float(row['avg_departure_delay'] or 0):.1f} min")
    c4.metric("Cancellation rate", f"{float(row['cancel_rate'] or 0):.2f}%")


def render_trend(where, params):
    grain = st.session_state["time_grain"]
    control1, control2 = st.columns([4, 1])
    with control1:
        st.selectbox(
            "Time grain (Roll-up / Drill-down): Year > Quarter > Month > Day > Hour",
            TIME_GRAINS,
            key="time_grain",
            on_change=on_time_grain_change,
        )
    with control2:
        st.button(
            "Roll up one level",
            key="trend_roll_up",
            on_click=roll_up_time,
            disabled=not st.session_state["time_drill"],
        )
    breadcrumb = "All periods"
    if st.session_state["time_drill"]:
        breadcrumb = "All periods > " + " > ".join(
            fmt_time_entry(entry) for entry in st.session_state["time_drill"]
        )
    st.caption(f"Drill path: {breadcrumb}  |  Grain: {grain}")

    frame, sql = fetch_trend(grain, where, params)
    if frame.empty:
        st.info("No trend data for the current slice.")
        return
    custom = [[row.y, row.q, row.m, row.d, row.h] for row in frame.itertuples(index=False)]
    fig = go.Figure()
    fig.add_bar(
        x=frame["period"],
        y=frame["avg_arrival_delay"],
        name="Avg arrival delay (min)",
        marker_color="#1f77b4",
        customdata=custom,
    )
    fig.add_bar(
        x=frame["period"],
        y=frame["avg_departure_delay"],
        name="Avg departure delay (min)",
        marker_color="#ff7f0e",
        customdata=custom,
    )
    fig.add_scatter(
        x=frame["period"],
        y=frame["arrival_cancel_rate"],
        name="Arrival cancelled (%)",
        yaxis="y2",
        mode="lines+markers",
        line=dict(color="#d62728", dash="dot"),
    )
    fig.add_scatter(
        x=frame["period"],
        y=frame["departure_cancel_rate"],
        name="Departure cancelled (%)",
        yaxis="y2",
        mode="lines+markers",
        line=dict(color="#2ca02c", dash="dot"),
    )
    fig.update_layout(
        barmode="group",
        height=430,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        yaxis=dict(title="Average delay (minutes)"),
        yaxis2=dict(overlaying="y", side="right", title="Cancelled (%)", rangemode="tozero"),
        margin=dict(t=30, b=10),
    )
    can_drill = TIME_INDEX[grain] < len(TIME_GRAINS) - 1
    if can_drill:
        st.caption("Click a bar to drill down to the next time level.")
    st.plotly_chart(
        fig,
        use_container_width=True,
        on_select="rerun" if can_drill else None,
        selection_mode="points" if can_drill else None,
        key="trend_chart",
    )
    with st.expander("View SQL - Time roll-up / drill-down query"):
        st.code(sql, language="sql")


def render_cancellations(where, params):
    level = st.session_state["dim_level"]
    control1, control2 = st.columns([4, 1])
    with control1:
        st.selectbox(
            "Service hierarchy (Roll-up / Drill-down): Company > Service Type > Train Number",
            DIM_LEVELS,
            key="dim_level",
            on_change=on_dim_level_change,
        )
    with control2:
        st.button(
            "Roll up one level",
            key="dim_roll_up",
            on_click=roll_up_dim,
            disabled=not st.session_state["dim_drill"],
        )
    breadcrumb = "All services"
    if st.session_state["dim_drill"]:
        breadcrumb = "All services > " + " > ".join(
            fmt_dim_entry(entry) for entry in st.session_state["dim_drill"]
        )
    st.caption(f"Drill path: {breadcrumb}  |  Level: {level}")

    frame, sql = fetch_cancellations(where, params)
    if frame.empty:
        st.info("No cancellation data for the current slice.")
        return
    frame = frame.copy()
    frame["member"] = frame["member"].astype(str)
    fig = px.bar(
        frame,
        x="member",
        y=["arrival_cancel_rate", "departure_cancel_rate"],
        barmode="group",
        labels={"value": "Cancelled stops (%)", "member": "", "variable": ""},
        color_discrete_map={
            "arrival_cancel_rate": "#d62728",
            "departure_cancel_rate": "#2ca02c",
        },
        hover_data={
            "stop_count": True,
            "avg_arrival_delay": ":.1f",
            "avg_departure_delay": ":.1f",
        },
    )
    fig.update_layout(
        height=420,
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        margin=dict(t=30, b=10),
    )
    can_drill = DIM_INDEX[level] < len(DIM_LEVELS) - 1
    if can_drill:
        st.caption("Click a bar to drill down to the next service level.")
    st.plotly_chart(
        fig,
        use_container_width=True,
        on_select="rerun" if can_drill else None,
        selection_mode="points" if can_drill else None,
        key="dim_chart",
    )
    with st.expander("View underlying numbers"):
        st.dataframe(
            frame[
                [
                    "member",
                    "stop_count",
                    "avg_arrival_delay",
                    "avg_departure_delay",
                    "arrival_cancel_rate",
                    "departure_cancel_rate",
                    "any_cancel_rate",
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )
    with st.expander("View SQL - Service hierarchy roll-up / drill-down query"):
        st.code(sql, language="sql")


def render_ranking(where, params):
    top_n = st.select_slider("Top N trains by delay severity", [10, 15, 25, 50, 100], value=15)
    frame, sql = fetch_ranking(where, params, top_n)
    if frame.empty:
        st.info("No trains match the current slice.")
        return
    frame = frame.copy()
    frame["train_label"] = (
        "T" + frame["train_number"].astype(str) + " | " + frame["company"].astype(str)
    )
    fig = px.bar(
        frame.sort_values("severity_score", ascending=True),
        x="severity_score",
        y="train_label",
        orientation="h",
        color="avg_departure_delay",
        color_continuous_scale="YlOrRd",
        labels={
            "severity_score": "Severity score (max of avg delays, minutes)",
            "train_label": "",
            "avg_departure_delay": "Avg dep. delay",
        },
        hover_data={
            "service_type": True,
            "stop_count": True,
            "avg_arrival_delay": ":.1f",
            "avg_departure_delay": ":.1f",
            "cancel_rate": ":.2f",
            "severity_score": ":.1f",
        },
    )
    fig.update_layout(height=max(420, 24 * len(frame)), margin=dict(t=20, b=10))
    st.plotly_chart(fig, use_container_width=True, key="rank_chart")
    with st.expander("View ranking table"):
        st.dataframe(
            frame[
                [
                    "train_number",
                    "company",
                    "service_type",
                    "stop_count",
                    "avg_arrival_delay",
                    "avg_departure_delay",
                    "severity_score",
                    "cancel_rate",
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )
    with st.expander("View SQL - Delay severity ranking query"):
        st.code(sql, language="sql")


def render_heatmap(where, params):
    metric_label = st.radio(
        "Heatmap metric",
        ["Avg arrival delay", "Avg departure delay", "Arrival cancelled %", "Departure cancelled %"],
        horizontal=True,
    )
    metric_col = {
        "Avg arrival delay": "avg_arrival_delay",
        "Avg departure delay": "avg_departure_delay",
        "Arrival cancelled %": "arrival_cancel_rate",
        "Departure cancelled %": "departure_cancel_rate",
    }[metric_label]
    frame, sql = fetch_heatmap(where, params)
    if frame.empty:
        st.info("No heatmap data for the current slice.")
        return
    pivot = frame.pivot_table(
        index="day_name",
        columns="hour_of_day",
        values=metric_col,
        aggfunc="mean",
    )
    day_order = (
        frame.sort_values("day_of_week").drop_duplicates("day_name")["day_name"].tolist()
    )
    pivot = pivot.reindex([d for d in day_order if d in pivot.index])
    fig = px.imshow(
        pivot,
        labels=dict(x="Hour of day", y="Day of week", color=metric_label),
        aspect="auto",
        color_continuous_scale="YlOrRd",
        text_auto=".1f",
    )
    fig.update_layout(height=380, margin=dict(t=20, b=10))
    st.plotly_chart(fig, use_container_width=True, key="heat_chart")
    with st.expander("View SQL - Day x Hour aggregation query"):
        st.code(sql, language="sql")


def main():
    init_state()
    if consume_time_selection() or consume_dim_selection():
        st.rerun()

    st.title("DutchRail Operations OLAP Dashboard")
    st.caption(
        "Analytics over the railway_dw warehouse loaded by STADVDB-MCO1-GROUP9-ETL.py: "
        "Warehouse joined to Location, Train and Stop on service_rdt_id / stop_rdt_id. "
        "Supports Slice, Dice, Roll-up and Drill-down against MySQL."
    )

    try:
        min_date, max_date = load_date_bounds()
        options = dimension_options()
    except Exception as exc:
        db.reset_pool()
        st.error(
            "Could not connect to the MySQL data warehouse. "
            "Verify MySQL is running and DB_* environment variables are set. "
            f"Details: {exc}"
        )
        st.stop()

    filters = render_sidebar(options, min_date, max_date)
    st.caption(f"Active slice/dice context: {active_context(filters)}")

    where, params = build_where(filters)

    kpis, _ = fetch_kpis(where, params)
    render_kpi_cards(kpis)
    st.divider()

    st.subheader("1. Delay Trend Over Time")
    render_trend(where, params)
    st.divider()

    left, right = st.columns(2)
    with left:
        st.subheader("2. Cancellation Rates by Company")
        render_cancellations(where, params)
    with right:
        st.subheader("3. Delay Severity Ranking by Train")
        render_ranking(where, params)

    st.divider()
    st.subheader("4. Hourly Delay Heatmap (Day x Hour)")
    render_heatmap(where, params)


if __name__ == "__main__":
    main()
