# app.py — Live Flight Delay & Cancellation Tracker (Aviationstack only)
# Run: streamlit run app.py

import os, requests, pandas as pd, streamlit as st
import plotly.express as px
import datetime as dt

st.set_page_config(page_title="Live Flight Delay Tracker", layout="wide")

# ─────────────────────────── API key ───────────────────────────
# ─────────────────────────── API key logic ───────────────────────────
API_KEY = ""
try:
    API_KEY = (
        os.getenv("AVIATIONSTACK_KEY", "") or
        st.secrets["api"].get("AVIATIONSTACK_KEY_PRIMARY", "") or
        st.secrets["api"].get("AVIATIONSTACK_KEY", "")  # legacy key
    )
    BACKUP_KEY = st.secrets["api"].get("AVIATIONSTACK_KEY_BACKUP", "")
except Exception:
    API_KEY = os.getenv("AVIATIONSTACK_KEY", "")
    BACKUP_KEY = ""

# Verify key validity with a quick test endpoint
def check_api_key(key: str) -> bool:
    try:
        r = requests.get("http://api.aviationstack.com/v1/flights", params={"access_key": key, "limit": 1}, timeout=8)
        j = r.json()
        if "error" in j and "code" in j["error"]:
            return False
        return r.ok
    except Exception:
        return False

# If the primary key fails, automatically try backup
if not API_KEY or not check_api_key(API_KEY):
    st.warning("Primary API key failed or over limit — switching to backup.")
    if BACKUP_KEY and check_api_key(BACKUP_KEY):
        API_KEY = BACKUP_KEY
    else:
        st.error("Both API keys are invalid or exhausted. Please update secrets.toml.")
        st.stop()
with st.sidebar:
    key_label = "Primary" if API_KEY == st.secrets["api"].get("AVIATIONSTACK_KEY_PRIMARY", "") else "Backup"
    st.caption(f"🔑 Using **{key_label} API key**")


# ───────────────────── Helper: fetch & normalize ───────────────
def _parse_dt(x):
    try:
        return pd.to_datetime(x, errors="coerce")
    except Exception:
        return pd.NaT

@st.cache_data(show_spinner=False, ttl=60)
def fetch_live_flights(api_key: str, airline=None, dep=None, arr=None,
                       status=None, flight_date=None, max_rows=200):
    """
    Pull flights from Aviationstack with optional airline/dep/arr/status and flight_date (YYYY-MM-DD).
    If your plan blocks flight_date, we auto-fallback to no date and set session flag 'date_fallback'.
    Returns normalized DataFrame with delay/cancel flags.
    """
    base_url = "http://api.aviationstack.com/v1/flights"

    # 0) Probe for date support (cheap single-request test)
    date_str = None
    if flight_date is not None:
        date_str = pd.to_datetime(flight_date).strftime("%Y-%m-%d")
        test_params = {"access_key": api_key, "limit": 1, "offset": 0}
        if airline: test_params["airline_iata"] = airline
        if dep and dep != "(Any)": test_params["dep_iata"] = dep
        if arr and arr != "(Any)": test_params["arr_iata"] = arr
        if status: test_params["flight_status"] = status
        test_params["flight_date"] = date_str
        try:
            tr = requests.get(base_url, params=test_params, timeout=12)
            tj = tr.json() if tr.headers.get("content-type","").startswith("application/json") else {}
            if isinstance(tj, dict) and "error" in tj and str(tj["error"].get("code","")) == "function_access_restricted":
                st.session_state["date_fallback"] = True
                st.warning("Your plan doesn’t support filtering by date (flight_date). Falling back to recent flights without a date filter.")
                date_str = None  # disable date filter
        except Exception:
            # If probe fails, just proceed without date filter
            date_str = None

    rows, offset, step = [], 0, 100

    while len(rows) < max_rows:
        params = {"access_key": api_key, "limit": step, "offset": offset}
        if airline: params["airline_iata"] = airline
        if dep and dep != "(Any)": params["dep_iata"] = dep
        if arr and arr != "(Any)": params["arr_iata"] = arr
        if status: params["flight_status"] = status
        if date_str: params["flight_date"] = date_str

        try:
            r = requests.get(base_url, params=params, timeout=15)

            # DEBUG surfacing
            try:
                j = r.json()
            except Exception:
                st.error(f"Non-JSON response from API (status {r.status_code}).")
                st.write("Params used:", params)
                return pd.DataFrame()

            if isinstance(j, dict) and "error" in j:
                err = j["error"]
                code = str(err.get("code"))
                msg = err.get("message")
                st.error(f"Aviationstack error: {code} – {msg}")
                st.write("Params used:", params)
                # If blocked by function_access_restricted (not caught by the probe), try once without date
                if code == "function_access_restricted" and date_str:
                    st.session_state["date_fallback"] = True
                    st.warning("Falling back: removing flight_date and retrying.")
                    date_str = None
                    rows, offset = [], 0
                    continue
                return pd.DataFrame()

            data = j.get("data") or []
        except Exception as e:
            st.error(f"Request failed: {e}")
            st.write("Params used:", params)
            return pd.DataFrame()

        if not data:
            break

        for d in data:
            dep_blk = d.get("departure") or {}
            arr_blk = d.get("arrival") or {}
            air_blk = d.get("airline") or {}

            sched_dep = _parse_dt(dep_blk.get("scheduled"))
            act_dep   = _parse_dt(dep_blk.get("actual"))
            sched_arr = _parse_dt(arr_blk.get("scheduled"))
            act_arr   = _parse_dt(arr_blk.get("actual"))

            dep_delay = dep_blk.get("delay")
            arr_delay = arr_blk.get("delay")
            if dep_delay is None and pd.notna(sched_dep) and pd.notna(act_dep):
                dep_delay = (act_dep - sched_dep).total_seconds() / 60.0
            if arr_delay is None and pd.notna(sched_arr) and pd.notna(act_arr):
                arr_delay = (act_arr - sched_arr).total_seconds() / 60.0

            rows.append({
                "carrier_name":  air_blk.get("name") or air_blk.get("iata") or "",
                "carrier_code":  air_blk.get("iata") or "",
                "origin":        dep_blk.get("iata"),
                "dest":          arr_blk.get("iata"),
                "status":        (d.get("flight_status") or "").lower(),  # scheduled/active/landed/cancelled/…
                "sched_dep":     sched_dep,
                "act_dep":       act_dep,
                "sched_arr":     sched_arr,
                "act_arr":       act_arr,
                "dep_delay":     float(dep_delay) if dep_delay is not None else None,
                "arr_delay":     float(arr_delay) if arr_delay is not None else None,
                "cancel_flag":   1 if str(d.get("flight_status") or "").lower() == "cancelled" else 0,
            })

        offset += step
        if len(data) < step or len(rows) >= max_rows:
            break

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    # Delayed (≥15 min) if either dep or arr delay hits threshold
    df["delayed_flag"] = (
        (pd.to_numeric(df["arr_delay"], errors="coerce") >= 15) |
        (pd.to_numeric(df["dep_delay"], errors="coerce") >= 15)
    ).astype(int)

    # Convenience: departure date
    df["dep_date"] = pd.to_datetime(df["sched_dep"], errors="coerce").dt.date
    return df

# ───────────────────────────── Sidebar UI ──────────────────────
with st.sidebar:
    st.header("Filters")

    airline = st.text_input("Airline IATA (optional)", value="DL").strip() or None

    airports = {
        "(Any)": "(Any)",
        "ATL": "Atlanta Hartsfield–Jackson",
        "DFW": "Dallas/Fort Worth",
        "DEN": "Denver",
        "ORD": "Chicago O’Hare",
        "LAX": "Los Angeles",
        "MIA": "Miami",
        "JFK": "New York JFK",
        "SEA": "Seattle–Tacoma",
        "BOS": "Boston Logan",
        "PHX": "Phoenix Sky Harbor",
        "CLT": "Charlotte Douglas",
        "IAH": "Houston Bush",
        "SFO": "San Francisco",
        "MSP": "Minneapolis–St. Paul",
        "DTW": "Detroit Metro",
        "BWI": "Baltimore/Washington",
        "LAS": "Las Vegas Harry Reid",
        "MCO": "Orlando",
    }
    def opt_label(code): return code if code == "(Any)" else f"{code} – {airports.get(code,'')}"
    dep_iata = st.selectbox("Departure airport", list(airports.keys()), index=1, format_func=opt_label)
    arr_iata = st.selectbox("Arrival airport",   list(airports.keys()), index=0, format_func=opt_label)

    status = st.selectbox("Flight status (optional)", ["", "scheduled", "active", "landed", "cancelled", "incident", "diverted"])
    status = status or None

    target_date = st.date_input("Date (used if your plan allows flight_date)", value=dt.date.today())

    # 🧪 Minimal connectivity test
    test_minimal = st.toggle("🧪 Minimal test (ignore date & status)", value=False,
                             help="Verifies your key/endpoint by fetching recent flights without date/status.")

    # Visualization controls
    top_n = st.slider("Show top N items", 5, 25, 10, step=5)

    max_rows = st.slider("Max rows to fetch", 50, 500, 200, step=50, help="Keep modest for free-tier limits.")
    fetch_btn = st.button("🔄 Fetch / Refresh", use_container_width=True)

# ─────────────────────────── Fetch on demand ───────────────────
if fetch_btn or "live_df" not in st.session_state:
    st.session_state.pop("date_fallback", None)  # reset note each fetch
    if test_minimal:
        st.session_state.live_df = fetch_live_flights(
            API_KEY, airline, dep_iata, arr_iata,
            status=None, flight_date=None, max_rows=max_rows
        )
    else:
        st.session_state.live_df = fetch_live_flights(
            API_KEY, airline, dep_iata, arr_iata,
            status=status, flight_date=target_date, max_rows=max_rows
        )

df = st.session_state.live_df
if df.empty:
    st.warning("No live flights returned for the current filters (or API limit reached). Try toggling 🧪 Minimal test or widening filters.")
    st.stop()

if st.session_state.get("date_fallback"):
    st.warning("Your plan doesn’t support filtering by date (flight_date). Falling back to recent flights without a date filter.")

with st.expander("Debug: show first 5 raw rows"):
    st.write(df.head(5))

# Working subset used for charts/table
sub = df.copy()

# ───────────────────── Visualization helper components ───────────────────────
def hbar_count(df, x_col, y_col, title, top_n=10, height_per_bar=36, x_suffix=""):
    """Horizontal bar with counts, value labels on bars, top N only."""
    if df.empty:
        st.info("No data to show.")
        return
    use = df.nlargest(top_n, y_col).copy()
    use = use.sort_values(y_col, ascending=True)  # largest on top visually
    fig = px.bar(
        use, x=y_col, y=x_col, orientation="h",
        labels={x_col:"", y_col:""},
        text=y_col, title=title, template="plotly_white",
    )
    fig.update_layout(
        height=max(260, int(len(use) * height_per_bar) + 120),
        margin=dict(l=10, r=10, t=60, b=10),
        xaxis_title="", yaxis_title="",
    )
    fig.update_traces(texttemplate="%{text}" + x_suffix, textposition="outside", cliponaxis=False)
    st.plotly_chart(fig, use_container_width=True)

def hbar_pct(df, x_col, y_col, title, top_n=10, height_per_bar=36):
    """Horizontal bar with % labels (0–100). y_col should be 0–1 fraction."""
    if df.empty:
        st.info("No data to show.")
        return
    use = df.nlargest(top_n, y_col).copy()
    use = use.sort_values(y_col, ascending=True)
    use["pct"] = (use[y_col] * 100).round(1)
    fig = px.bar(
        use, x="pct", y=x_col, orientation="h",
        labels={x_col:"", "pct":""},
        text="pct", title=title, template="plotly_white",
    )
    fig.update_layout(
        height=max(260, int(len(use) * height_per_bar) + 120),
        margin=dict(l=10, r=10, t=60, b=10),
        xaxis_title="", yaxis_title="",
        xaxis=dict(ticksuffix="%", range=[0, max(100, (use["pct"].max() // 10 + 1) * 10)]),
    )
    fig.update_traces(texttemplate="%{text}%", textposition="outside", cliponaxis=False)
    st.plotly_chart(fig, use_container_width=True)

def pie_count(df, label_col, value_col, title, hole=0.45, top_n=8):
    """Donut pie: groups tail into 'Other' for readability."""
    if df.empty:
        st.info("No data to show.")
        return
    use = df.sort_values(value_col, ascending=False)
    head, tail = use.head(top_n), use.iloc[top_n:]
    if not tail.empty:
        other_sum = tail[value_col].sum()
        head = pd.concat([head, pd.DataFrame({label_col: ["Other"], value_col: [other_sum]})], ignore_index=True)
    fig = px.pie(
        head, names=label_col, values=value_col, hole=hole,
        title=title, template="plotly_white"
    )
    fig.update_traces(textposition='inside', textinfo='percent+label')
    fig.update_layout(margin=dict(l=10,r=10,t=60,b=10), height=420)
    st.plotly_chart(fig, use_container_width=True)

def pie_binary(n1, n2, labels=("A","B"), title=""):
    """Simple 2-slice donut, e.g., Delayed vs Not delayed."""
    dfp = pd.DataFrame({ "label": [labels[0], labels[1]], "value": [n1, n2] })
    fig = px.pie(dfp, names="label", values="value", hole=0.5, title=title, template="plotly_white")
    fig.update_traces(textposition='inside', textinfo='percent+label')
    fig.update_layout(margin=dict(l=10,r=10,t=60,b=10), height=380)
    st.plotly_chart(fig, use_container_width=True)

# ───────────────────────── KPIs: only when Status = "scheduled" ──────────────
if status == "scheduled":
    # Fetch ALL statuses for the same filters to get proper denominators
    all_today = fetch_live_flights(
        API_KEY, airline=airline, dep=dep_iata, arr=arr_iata,
        status=None, flight_date=target_date, max_rows=500
    )

    used_fallback = bool(st.session_state.get("date_fallback"))

    scoped = all_today.copy()
    if dep_iata and dep_iata != "(Any)":
        scoped = scoped[scoped["origin"] == dep_iata]
    if arr_iata and arr_iata != "(Any)":
        scoped = scoped[scoped["dest"] == arr_iata]
    if airline:
        scoped = scoped[scoped["carrier_code"] == airline]

    total_scheduled_today = len(scoped)
    canceled_today = int(scoped["cancel_flag"].sum())

    # “Observed so far” = flights with actual times OR delay values OR cancelled
    observed = scoped[
        scoped["cancel_flag"].eq(1) |
        scoped["act_dep"].notna() | scoped["act_arr"].notna() |
        scoped["dep_delay"].notna() | scoped["arr_delay"].notna()
    ]
    delayed_today = int(observed["delayed_flag"].sum())
    avg_arr = pd.to_numeric(observed["arr_delay"], errors="coerce").mean()

    cancel_prob = (canceled_today / total_scheduled_today * 100.0) if total_scheduled_today else 0.0
    delay_prob  = (delayed_today  / len(observed)           * 100.0) if len(observed) else 0.0

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Scheduled (scope)", f"{total_scheduled_today:,}")
    m2.metric("Observed so far", f"{len(observed):,}")
    m3.metric("Cancel probability", f"{cancel_prob:.2f}%")
    m4.metric("Delay ≥15 min (observed)", f"{delay_prob:.1f}%")

    if used_fallback:
        st.caption("Note: Your plan doesn’t allow flight_date. KPIs use a recent snapshot (no exact date filter).")
    else:
        st.caption("KPIs computed for the selected date (same airline/route). Delay % uses observed flights; Cancel % uses all scheduled.")
else:
    st.info("KPIs are shown only when Status = 'scheduled' (otherwise they’d be tautological).")

# ───────────────────────── Status Summary (tabs + pies + delays) ───────────────────────
st.divider()
st.subheader("Status summary")

def render_status_tab(df_tab: pd.DataFrame, label: str):
    total = len(df_tab)
    delayed = int(pd.to_numeric(df_tab.get("delayed_flag", 0), errors="coerce").fillna(0).sum())
    cancelled = int(pd.to_numeric(df_tab.get("cancel_flag", 0), errors="coerce").fillna(0).sum())

    c1, c2, c3 = st.columns(3)
    c1.metric("Flights", f"{total:,}")
    c2.metric("Delayed ≥15 min", f"{delayed:,}")
    c3.metric("Cancelled", f"{cancelled:,}")

    if total == 0:
        st.info(f"No {label.lower()} flights for the current filters.")
        return

    # Pies: airline share + delayed vs not delayed
    by_airline = (
        df_tab.groupby("carrier_name", as_index=False)
              .size().rename(columns={"size":"flights"})
    )
    colA, colB = st.columns(2)
    with colA:
        pie_count(by_airline, "carrier_name", "flights", f"Share by airline — {label}", top_n=10)
    with colB:
        pie_binary(delayed, total - delayed, labels=("Delayed ≥15m","Not delayed"),
                   title=f"Delay mix — {label}")

    # Cancelled-only extra: which airlines account for cancels
    if label.lower() == "cancelled":
        by_airline_cancel = (
            df_tab.groupby("carrier_name", as_index=False)
                  .size().rename(columns={"size":"cancelled"})
        )
        pie_count(by_airline_cancel, "carrier_name", "cancelled",
                  "Which airlines account for cancellations", top_n=10)

    # Top routes table (clean, ranked)
    by_route = (
        df_tab.assign(route=df_tab["origin"].fillna("?") + " → " + df_tab["dest"].fillna("?"))
              .groupby("route", as_index=False)
              .size().rename(columns={"size":"flights"})
              .sort_values("flights", ascending=False)
              .head(top_n)
    )
    st.caption(f"Top routes — {label}")
    st.dataframe(by_route, use_container_width=True)

def render_delay_tab(df_base: pd.DataFrame, airline_code: str | None):
    """Consolidated delay visuals (moved from the bottom). Uses current filters."""
    if df_base.empty:
        st.info("No flights in the current selection.")
        return

    st.markdown("### Delay probability")
    # By carrier
    by_carrier = (
        df_base.groupby("carrier_name", as_index=False)
               .agg(total=("status","count"),
                    delayed=("delayed_flag","sum"),
                    cancelled=("cancel_flag","sum"),
                    avg_arr_delay=("arr_delay","mean"))
    )
    if by_carrier.empty:
        st.info("No carrier data for current selection.")
    else:
        by_carrier["delay_probability"] = by_carrier["delayed"] / by_carrier["total"]
        hbar_pct(by_carrier, "carrier_name", "delay_probability",
                 "Delay probability by carrier", top_n=top_n)

    # By route for selected carrier (optional)
    if airline_code:
        st.markdown(f"### Delay probability by route — {airline_code}")
        carrier_df = df_base[df_base["carrier_code"] == airline_code]
        by_route = (
            carrier_df.groupby(["origin","dest"], as_index=False)
                      .agg(total=("status","count"),
                           delayed=("delayed_flag","sum"),
                           cancelled=("cancel_flag","sum"),
                           avg_arr_delay=("arr_delay","mean"))
        )
        if by_route.empty:
            st.info("No routes for that carrier in current selection.")
        else:
            by_route["route"] = by_route["origin"].fillna("?") + " → " + by_route["dest"].fillna("?")
            by_route["delay_probability"] = by_route["delayed"] / by_route["total"]
            hbar_pct(by_route, "route", "delay_probability",
                     f"Delay probability by route — {airline_code}", top_n=top_n)

# Build tabs (added a final 'Delays' tab)
status_order = ["All", "scheduled", "active", "landed", "cancelled", "incident", "diverted", "Delays"]
tabs = st.tabs([s.capitalize() for s in status_order])

for s, tab in zip(status_order, tabs):
    with tab:
        if s == "All":
            df_tab, label = sub, "All statuses"
            render_status_tab(df_tab, label)
        elif s == "Delays":
            render_delay_tab(sub, airline)
        else:
            df_tab, label = sub[sub["status"] == s], s.capitalize()
            render_status_tab(df_tab, label)


# ─────────────────────────── Live table (pretty) ─────────────────
st.divider()
st.subheader("Live flights (sample)")

show = sub.copy()
show_cols = ["carrier_name","carrier_code","origin","dest","status","dep_delay","arr_delay","sched_dep","act_dep","sched_arr","act_arr"]
show = show[show_cols]

for col in ["sched_dep","act_dep","sched_arr","act_arr"]:
    show[col] = pd.to_datetime(show[col], errors="coerce").dt.strftime("%b %d, %Y %I:%M %p")

st.dataframe(show, use_container_width=True)

st.caption("Notes: Delay = dep/arr ≥ 15 min. If your plan blocks flight_date, the app auto-falls back to a recent snapshot.")
