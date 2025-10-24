# app.py — Live Flight Delay & Cancellation Tracker (Aviationstack only)
# Run: streamlit run app.py

import os, requests, pandas as pd, streamlit as st
import plotly.express as px

st.set_page_config(page_title="Live Flight Delay Tracker", layout="wide")

# ─────────────────────────── API key ───────────────────────────
API_KEY = os.getenv("AVIATIONSTACK_KEY", "")
if not API_KEY:
    try:
        API_KEY = st.secrets["api"]["AVIATIONSTACK_KEY"]
    except Exception:
        API_KEY = ""

st.title("✈️ Live Flight Delay & Cancellation Tracker")

if not API_KEY:
    st.error("Missing API key. Add AVIATIONSTACK_KEY to .streamlit/secrets.toml or your env.")
    st.stop()

# ───────────────────── Helper: fetch & normalize ───────────────
def _parse_dt(x):
    try:
        return pd.to_datetime(x, errors="coerce")
    except Exception:
        return pd.NaT

@st.cache_data(show_spinner=False, ttl=60)
def fetch_live_flights(api_key: str, airline=None, dep=None, arr=None, status=None, max_rows=200):
    """
    Pull recent flights from Aviationstack. Keep filters tight to respect free-tier limits.
    Returns normalized DataFrame with delay/cancel flags.
    """
    base_url = "http://api.aviationstack.com/v1/flights"
    rows, offset, step = [], 0, 100

    while len(rows) < max_rows:
        params = {"access_key": api_key, "limit": step, "offset": offset}
        if airline: params["airline_iata"] = airline
        if dep and dep != "(Any)": params["dep_iata"] = dep
        if arr and arr != "(Any)": params["arr_iata"] = arr
        if status: params["flight_status"] = status

        try:
            r = requests.get(base_url, params=params, timeout=15)
            j = r.json()
            data = j.get("data") or []
        except Exception:
            break

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
        if len(data) < step:
            break

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    # Delayed (≥15 min) if either dep or arr delay hits threshold
    df["delayed_flag"] = (
        (pd.to_numeric(df["arr_delay"], errors="coerce") >= 15) |
        (pd.to_numeric(df["dep_delay"], errors="coerce") >= 15)
    ).astype(int)

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

    max_rows = st.slider("Max rows to fetch", 50, 500, 200, step=50, help="Keep modest for free-tier limits.")
    fetch_btn = st.button("🔄 Fetch / Refresh", use_container_width=True)

# ─────────────────────────── Fetch on demand ───────────────────
if fetch_btn or "live_df" not in st.session_state:
    st.session_state.live_df = fetch_live_flights(API_KEY, airline, dep_iata, arr_iata, status, max_rows)

df = st.session_state.live_df
if df.empty:
    st.warning("No live flights returned for the current filters (or API limit reached). Try different filters.")
    st.stop()

# ───────────────────────── Route selection & KPIs ───────────────
# Require both dep & arr for a clear route metric; otherwise show overall snapshot.
need_route = (dep_iata != "(Any)") and (arr_iata != "(Any)")
sub = df.copy()
if need_route:
    sub = sub[(sub["origin"] == dep_iata) & (sub["dest"] == arr_iata)]

st.subheader("Metrics")

def metric_block(ttl: int, dly: int, cnl: int, avg_arr):
    tot = int(ttl)
    prob_delay  = (dly / tot * 100.0) if tot else 0.0
    prob_cancel = (cnl / tot * 100.0) if tot else 0.0
    mcols = st.columns(4)
    mcols[0].metric("Flights analyzed", f"{tot:,}")
    mcols[1].metric("Delay probability",  f"{prob_delay:.1f}%")
    mcols[2].metric("Cancel probability", f"{prob_cancel:.2f}%")
    mcols[3].metric("Avg arrival delay (min)", f"{avg_arr:.1f}" if pd.notna(avg_arr) else "—")

ttl = len(sub)
dly = int(sub["delayed_flag"].sum())
cnl = int(sub["cancel_flag"].sum())
avg_arr = pd.to_numeric(sub["arr_delay"], errors="coerce").mean()

route_label = f"{dep_iata} → {arr_iata}" if need_route else "(All routes in result set)"
st.write(f"**Selection:** {route_label}  •  Airline: {airline or 'Any'}  •  Status: {status or 'Any'}")
metric_block(ttl, dly, cnl, avg_arr)

# ───────────────────────── Comparison bars ──────────────────────
st.divider()

# 1) By carrier on this route (or on all results if no specific route)
st.subheader("Delay probability by carrier")
by_carrier = (
    sub.groupby("carrier_name", as_index=False)
       .agg(total=("status","count"), delayed=("delayed_flag","sum"), cancelled=("cancel_flag","sum"),
            avg_arr_delay=("arr_delay","mean"))
)
if by_carrier.empty:
    st.info("No carrier data for current selection.")
else:
    by_carrier["delay_probability"] = by_carrier["delayed"] / by_carrier["total"]
    fig1 = px.bar(by_carrier.sort_values("delay_probability", ascending=False),
                  x="carrier_name", y="delay_probability",
                  hover_data=["total","delayed","cancelled","avg_arr_delay"],
                  labels={"carrier_name":"Carrier","delay_probability":"Delay probability"})
    fig1.update_layout(yaxis_tickformat=".0%", plot_bgcolor="white", height=360, margin=dict(l=10,r=10,t=40,b=10))
    st.plotly_chart(fig1, use_container_width=True)

# 2) By route for selected carrier (if airline chosen)
if airline:
    st.subheader(f"Delay probability by route for {airline}")
    carrier_df = df[df["carrier_code"] == airline]
    by_route = (
        carrier_df.groupby(["origin","dest"], as_index=False)
                  .agg(total=("status","count"), delayed=("delayed_flag","sum"),
                       cancelled=("cancel_flag","sum"), avg_arr_delay=("arr_delay","mean"))
    )
    if by_route.empty:
        st.info("No routes for that carrier in current fetch.")
    else:
        by_route["delay_probability"] = by_route["delayed"] / by_route["total"]
        by_route["route"] = by_route["origin"].fillna("?") + " → " + by_route["dest"].fillna("?")
        fig2 = px.bar(by_route.sort_values("delay_probability", ascending=False).head(20),
                      x="route", y="delay_probability",
                      hover_data=["total","delayed","cancelled","avg_arr_delay"],
                      labels={"route":"Route","delay_probability":"Delay probability"})
        fig2.update_layout(yaxis_tickformat=".0%", plot_bgcolor="white", height=360, margin=dict(l=10,r=10,t=40,b=10))
        st.plotly_chart(fig2, use_container_width=True)

# ─────────────────────────── Live table (pretty) ─────────────────
st.divider()
st.subheader("Live flights (sample)")

show = sub.copy()
show_cols = ["carrier_name","carrier_code","origin","dest","status","dep_delay","arr_delay","sched_dep","act_dep","sched_arr","act_arr"]
show = show[show_cols]

# Format datetimes for readability
for col in ["sched_dep","act_dep","sched_arr","act_arr"]:
    show[col] = pd.to_datetime(show[col], errors="coerce").dt.strftime("%b %d, %Y %I:%M %p")

st.dataframe(show, use_container_width=True)

st.caption("Notes: Delay = dep/arr ≥ 15 min. Results are snapshots of recent flights from Aviationstack (rate limits apply).")
