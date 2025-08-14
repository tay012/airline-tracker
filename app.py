# app.py — Simple Flight Delay Chance Calculator (loads API key from .env)
# Run with: streamlit run app.py

import os
import time
import requests
import streamlit as st
import pandas as pd
import plotly.express as px
from dotenv import load_dotenv

# ---------- Load environment (.env) ----------
load_dotenv()  # reads .env if present
AVIATIONSTACK_KEY = os.getenv("AVIATIONSTACK_KEY", "").strip()

# ---------- Config ----------
DEFAULT_DATA_PATH = "/Users/angeltay/Desktop/projects/airline_delay_cause.csv"   # put your default CSV here
REQUIRED_COLS = ["carrier_name", "airport_name", "arr_flights", "arr_del15", "arr_delay"]
YEAR_CANDIDATES  = ["year", "yr"]
MONTH_CANDIDATES = ["month", "mnth"]

st.set_page_config(page_title="Flight Delay Chance (Simple)", layout="wide")
st.title("✈️ Flight Delay Chance")

# ---------- Helpers ----------
def standardize_cols(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = (
        out.columns.str.strip()
        .str.lower()
        .str.replace(" ", "_")
        .str.replace("-", "_")
    )
    return out

def resolve_one(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for c in candidates:
        if c in df.columns:
            return c
    return None

@st.cache_data
def load_csv_auto(default_path: str | None, upload) -> pd.DataFrame:
    """Priority: 1) user upload, 2) default file, else error."""
    if upload is not None:
        df = pd.read_csv(upload)
        return standardize_cols(df)
    if default_path and os.path.exists(default_path):
        df = pd.read_csv(default_path)
        return standardize_cols(df)
    raise FileNotFoundError(
        "No data found. Upload a CSV or place one at data/airline_delays_sample.csv"
    )

def faa_airport_status(iata_code: str) -> dict | None:
    """FAA Airport Status API (no key). Example: 'RIC', 'ATL', 'JFK'."""
    if not iata_code:
        return None
    try:
        url = f"https://soa.smext.faa.gov/asws/api/airport/status/{iata_code.upper()}"
        r = requests.get(url, timeout=8)
        if r.ok:
            return r.json()
    except Exception:
        return None
    return None

def aviationstack_flight(flight_number: str, api_key: str) -> dict | None:
    """Aviationstack flight by IATA number (e.g., 'DL123')."""
    if not (flight_number and api_key):
        return None
    try:
        url = "http://api.aviationstack.com/v1/flights"
        params = {"access_key": api_key, "flight_iata": flight_number.upper()}
        r = requests.get(url, params=params, timeout=10)
        if r.ok:
            data = r.json().get("data") or []
            return data[0] if data else None
    except Exception:
        return None
    return None

def risk_score(baseline_prob: float, faa_status: dict | None, live_phase: str | None) -> float:
    """Blend historical probability (0–1) with simple live signals."""
    score = float(baseline_prob or 0.0)
    if isinstance(faa_status, dict):
        delay = str(faa_status.get("Delay", "")).lower()
        reason = str((faa_status.get("Status") or {}).get("Reason", "")).lower()
        if "ground stop" in delay or "gs" in reason:
            score = min(1.0, score + 0.35)
        elif "ground delay" in delay or "edct" in reason:
            score = min(1.0, score + 0.20)
        elif any(k in reason for k in ["arrival", "depart"]):
            score = min(1.0, score + 0.10)
    if live_phase and any(k in live_phase.lower() for k in ["scheduled", "delayed", "on gate", "boarding"]):
        score = min(1.0, score + 0.05)
    return max(0.0, min(1.0, score))

# ---------- Sidebar (minimal, no nagging) ----------
with st.sidebar:
    st.header("Data")
    uploaded = st.file_uploader("Upload CSV (optional)", type=["csv"])
    st.caption("If you don't upload, the app uses the built-in default dataset automatically.")

    st.divider()
    st.header("Live (optional)")
    # If key is present in .env, show a green check; otherwise offer an input once.
    if AVIATIONSTACK_KEY:
        st.success("Aviationstack key loaded from .env")
        aviation_key = AVIATIONSTACK_KEY
    else:
        aviation_key = st.text_input("Aviationstack API key (optional)", type="password").strip()
        if aviation_key:
            st.info("Tip: put this in a .env file as AVIATIONSTACK_KEY so you don't type it again.")
    arrival_iata = st.text_input("Arrival IATA for FAA status (optional)", value="")  # e.g., RIC

# ---------- Load data ----------
try:
    raw = load_csv_auto(DEFAULT_DATA_PATH, uploaded)
except Exception as e:
    st.error(str(e))
    st.stop()

# ---------- Resolve columns (quiet auto) ----------
carrier_col     = resolve_one(raw, ["carrier_name", "carrier", "op_unique_carrier_name", "op_carrier"]) or "carrier_name"
airport_col     = resolve_one(raw, ["airport_name", "dest_airport_name", "origin_airport_name", "airport", "dest", "origin"]) or "airport_name"
arr_flights_col = resolve_one(raw, ["arr_flights", "flights", "num_flights"]) or "arr_flights"
arr_del15_col   = resolve_one(raw, ["arr_del15", "arr_del_15", "late_flights", "delayed_flights"]) or "arr_del15"
arr_delay_col   = resolve_one(raw, ["arr_delay", "arrival_delay", "arrdelay", "arr_delay_minutes", "avg_arr_delay"]) or "arr_delay"
year_col        = resolve_one(raw, YEAR_CANDIDATES)   # optional
month_col       = resolve_one(raw, MONTH_CANDIDATES)  # optional

# ---------- Basic type cleanup ----------
for col in [arr_flights_col, arr_del15_col, arr_delay_col, year_col, month_col]:
    if col in raw.columns:
        raw[col] = pd.to_numeric(raw[col], errors="coerce")

df = raw.dropna(subset=[carrier_col, airport_col, arr_flights_col, arr_del15_col, arr_delay_col]).copy()
df = df[df[arr_flights_col] > 0]

# ---------- Aggregate carrier × airport ----------
grouped = (
    df.groupby([carrier_col, airport_col], as_index=False)
      .agg(total_flights=(arr_flights_col, "sum"),
           delayed_flights=(arr_del15_col, "sum"),
           avg_delay=(arr_delay_col, "mean"))
)
grouped["delay_probability"] = grouped["delayed_flights"] / grouped["total_flights"]

# ---------- Simple selectors (only valid pairs) ----------
st.subheader("Pick a carrier & airport")
c1, c2 = st.columns(2)
with c1:
    sel_carrier = st.selectbox("Carrier", sorted(grouped[carrier_col].unique()))
with c2:
    valid_airports = sorted(grouped.loc[grouped[carrier_col] == sel_carrier, airport_col].unique())
    sel_airport = st.selectbox("Airport", valid_airports)

sel = grouped[(grouped[carrier_col] == sel_carrier) & (grouped[airport_col] == sel_airport)]
if sel.empty:
    st.warning("No data for that pair.")
    st.stop()

# ---------- Metrics ----------
prob = float(sel["delay_probability"].iloc[0]) * 100
avg_d = float(sel["avg_delay"].iloc[0])
tot   = int(sel["total_flights"].iloc[0])
dly   = int(sel["delayed_flights"].iloc[0])

m1, m2, m3 = st.columns(3)
m1.metric("Delay probability", f"{prob:.1f}%")
m2.metric("Avg delay (min)", f"{avg_d:.1f}")
m3.metric("Flights analyzed", f"{tot:,}")
st.caption(f"Delayed flights: {dly:,} / {tot:,}")

st.divider()

# ---------- Comparisons ----------
st.subheader(f"Carriers at {sel_airport}")
peers_airport = grouped[grouped[airport_col] == sel_airport].sort_values("delay_probability", ascending=False)
fig1 = px.bar(peers_airport, x=carrier_col, y="delay_probability",
              hover_data=["total_flights", "avg_delay"],
              labels={carrier_col:"Carrier", "delay_probability": "Delay probability"})
fig1.update_layout(xaxis_tickangle=-35, yaxis_tickformat=".0%", plot_bgcolor="white", height=380)
st.plotly_chart(fig1, use_container_width=True)

st.subheader(f"Airports for {sel_carrier}")
peers_carrier = grouped[grouped[carrier_col] == sel_carrier].sort_values("delay_probability", ascending=False)
fig2 = px.bar(peers_carrier, x=airport_col, y="delay_probability",
              hover_data=["total_flights", "avg_delay"],
              labels={airport_col:"Airport", "delay_probability": "Delay probability"})
fig2.update_layout(xaxis_tickangle=-35, yaxis_tickformat=".0%", plot_bgcolor="white", height=380)
st.plotly_chart(fig2, use_container_width=True)

# ---------- Optional monthly trends (auto if year/month exist) ----------
st.divider()
st.subheader("Trends (if year & month exist)")
if (year_col in df.columns if year_col else False) and (month_col in df.columns if month_col else False):
    base = df[(df[carrier_col] == sel_carrier) & (df[airport_col] == sel_airport)].dropna(subset=[year_col, month_col]).copy()
    if not base.empty:
        base[year_col] = base[year_col].astype(int)
        base[month_col] = base[month_col].astype(int)
        trend = (
            base.groupby([year_col, month_col], as_index=False)
                .agg(flights=(arr_flights_col, "sum"),
                     delayed=(arr_del15_col, "sum"),
                     avg_delay=(arr_delay_col, "mean"))
        )
        trend["delay_probability"] = trend["delayed"] / trend["flights"]
        trend["date"] = pd.to_datetime(trend[year_col].astype(str) + "-" + trend[month_col].astype(str) + "-01")
        trend = trend.sort_values("date")

        figp = px.line(trend, x="date", y="delay_probability", markers=True,
                       labels={"date": "Month", "delay_probability": "Delay probability"})
        figp.update_layout(yaxis_tickformat=".0%", plot_bgcolor="white", height=350)
        st.plotly_chart(figp, use_container_width=True)

        figd = px.line(trend, x="date", y="avg_delay", markers=True,
                       labels={"date": "Month", "avg_delay": "Avg delay (min)"})
        figd.update_layout(plot_bgcolor="white", height=350)
        st.plotly_chart(figd, use_container_width=True)
    else:
        st.caption("No monthly data for this pair.")
else:
    st.caption("Dataset doesn’t include year/month, so trends are hidden.")

# ---------- Live (appears only if user supplies flight or FAA IATA) ----------
st.divider()
st.subheader("Live tracking (optional)")
colA, colB, colC = st.columns([1,1,1])
with colA:
    flight_num = st.text_input("Flight number (e.g., DL123)", value="")
with colB:
    # aviation_key is either from .env (green check) or text_input above
    aviation_key = AVIATIONSTACK_KEY or ""
with colC:
    arrival_iata = st.text_input("Arrival IATA for FAA", value="").strip()

show_live = bool(flight_num and aviation_key)
if show_live or arrival_iata:
    faa = faa_airport_status(arrival_iata) if arrival_iata else None
    live_info = aviationstack_flight(flight_num, aviation_key) if show_live else None
    live_phase = (live_info.get("flight_status") if live_info else None) or ""

    k1, k2, k3 = st.columns(3)
    with k1:
        st.markdown("**Arrival airport (FAA)**")
        if faa:
            st.write(f"**{faa.get('IATA','')}** — {faa.get('Name','')}")
            st.write(f"Delay: {faa.get('Delay','')}")
            st.write(f"Reason: {((faa.get('Status') or {}).get('Reason')) or '—'}")
        else:
            st.write("—")

    with k2:
        st.markdown("**Live flight**")
        if live_info:
            dep = (live_info.get("departure") or {}).get("iata") or "—"
            arr = (live_info.get("arrival") or {}).get("iata") or "—"
            st.write(f"Airline: {((live_info.get('airline') or {}).get('name')) or '—'}")
            st.write(f"Status: **{(live_info.get('flight_status') or '—').title()}**")
            st.write(f"Route: {dep} → {arr}")
        else:
            st.write("—")

    with k3:
        baseline = float(sel["delay_probability"].iloc[0]) if not sel.empty else 0.0
        risk = risk_score(baseline, faa, live_phase)
        st.metric("Estimated delay risk", f"{100*risk:.1f}%")
        st.caption(f"Baseline from history: {100*baseline:.1f}%")
