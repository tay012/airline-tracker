# app.py — Flight Delay Chance (with cancellations + reasons, cloud-safe)
# Run: streamlit run app.py

import os
import requests
import streamlit as st
import pandas as pd
import plotly.express as px

# --- optional .env support (safe if missing on Cloud) ---
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# --- Mobile-friendly settings ---
st.set_page_config(page_title="Flight Delay & Cancellation Risk",
                   layout="wide",
                   initial_sidebar_state="collapsed")

# Toggle to optimize layout for small screens (iPhone)
with st.sidebar:
    MOBILE = st.toggle("📱 Mobile mode (iPhone)", value=True, help="Optimizes spacing and chart sizes for phones")

# CSS tweaks for small screens & better touch targets
mobile_css = f"""
<style>
/* Make content breathe less on small screens */
@media (max-width: 640px) {{
  .block-container {{ padding: 0.6rem 0.6rem !important; }}
  h1, h2, h3 {{ line-height: 1.2; }}
}}

/* Bigger tap targets for select boxes, radios, buttons */
.stButton>button, .stDownloadButton>button {{
  padding: { '0.9rem 1rem' if MOBILE else '0.55rem 0.8rem' };
  border-radius: 12px;
}}
.stRadio > div[role='radiogroup'] label, label {{
  font-size: { '1.0rem' if MOBILE else '0.95rem' };
}}

/* Let charts fill width and avoid horizontal scroll */
.stPlotlyChart {{ width: 100% !important; }}

/* Center metrics on narrow screens */
@media (max-width: 640px) {{
  div[data-testid="metric-container"] {{ text-align: center; }}
  div[data-testid="stHorizontalBlock"] > div {{ width: 100% !important; display:block; }}
}}

/* Make sidebar semi-translucent and easier to read on phones */
[data-testid="stSidebar"] > div:first-child {{
  backdrop-filter: blur(4px);
}}
</style>
"""
st.markdown(mobile_css, unsafe_allow_html=True)

# Helper to pick sensible sizes
def ui_sizes(mobile: bool):
    return {
        "chart_h": 260 if mobile else 380,
        "chart_h_trend": 250 if mobile else 350,
        "tickangle": -25 if mobile else -35,
    }

UI = ui_sizes(MOBILE)


# API key from env first, then Streamlit Secrets (Cloud)
API_KEY = os.getenv("AVIATIONSTACK_KEY", "")
if not API_KEY:
    try:
        API_KEY = st.secrets.get("AVIATIONSTACK_KEY", "")
    except Exception:
        API_KEY = ""

st.set_page_config(page_title="Flight Delay & Cancellation Risk", layout="wide")
st.title("✈️ Flight Delay & Cancellation Risk")

# ---------- Default dataset path (CASE-SENSITIVE) ----------
DEFAULT_DATA_PATH = "data/Airline_Delay_Cause.csv"

# Optional time cols
YEAR_CANDIDATES  = ["year", "yr"]
MONTH_CANDIDATES = ["month", "mnth"]

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
    """
    Priority:
      1) User-uploaded CSV
      2) Default CSV committed to repo at data/Airline_Delay_Cause.csv
      3) Tiny inline sample (last-resort so app still runs)
    """
    if upload is not None:
        df = pd.read_csv(upload)
        return standardize_cols(df)

    if default_path and os.path.exists(default_path):
        df = pd.read_csv(default_path)
        return standardize_cols(df)

    # fallback sample so app renders even if file missing on Cloud
    from io import StringIO
    sample = StringIO("""carrier_name,airport_name,arr_flights,arr_del15,arr_delay,year,month,cancellation_code
Delta Air Lines,Richmond,100,20,8.5,2023,6,A
United Airlines,Richmond,80,10,6.2,2023,6,B
Delta Air Lines,Atlanta,150,25,7.9,2023,6,
United Airlines,Atlanta,120,18,5.3,2023,6,C
""")
    df = pd.read_csv(sample)
    return standardize_cols(df)

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

def risk_score_delay(baseline_prob: float, faa_status: dict | None, live_phase: str | None) -> float:
    """Blend historical delay probability (0–1) with simple live signals."""
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

def risk_score_cancel(baseline_prob: float, faa_status: dict | None, live_phase: str | None) -> float:
    """Very simple cancellation heuristic (tune as desired)."""
    score = float(baseline_prob or 0.0)
    if isinstance(faa_status, dict):
        delay = str(faa_status.get("Delay", "")).lower()
        reason = str((faa_status.get("Status") or {}).get("Reason", "")).lower()
        if "ground stop" in delay or "gs" in reason:
            score = min(1.0, score + 0.25)
        elif "ground delay" in delay or "edct" in reason:
            score = min(1.0, score + 0.10)
    if live_phase and "cancel" in live_phase.lower():
        score = 1.0
    return max(0.0, min(1.0, score))

# ---------- Sidebar ----------
with st.sidebar:
    st.header("Data")
    uploaded = st.file_uploader("Upload CSV (optional)", type=["csv"])
    st.caption("If you don’t upload, the app uses data/Airline_Delay_Cause.csv automatically (if present).")

    st.divider()
    st.header("Live (optional)")
    if API_KEY:
        st.success("Aviationstack key loaded from environment/secrets.")
    else:
        st.info("No Aviationstack key found. Live flight lookup will be disabled.")
    arrival_iata = st.text_input("Arrival IATA for FAA status (optional)", value="").strip()

# ---------- Load data ----------
raw = load_csv_auto(DEFAULT_DATA_PATH, uploaded)

# ---------- Resolve columns (quiet auto) ----------
carrier_col     = resolve_one(raw, ["carrier_name", "carrier", "op_unique_carrier_name", "op_carrier"]) or "carrier_name"
airport_col     = resolve_one(raw, ["airport_name", "dest_airport_name", "origin_airport_name", "airport", "dest", "origin"]) or "airport_name"
arr_flights_col = resolve_one(raw, ["arr_flights", "flights", "num_flights"]) or "arr_flights"
arr_del15_col   = resolve_one(raw, ["arr_del15", "arr_del_15", "late_flights", "delayed_flights"]) or "arr_del15"
arr_delay_col   = resolve_one(raw, ["arr_delay", "arrival_delay", "arrdelay", "arr_delay_minutes", "avg_arr_delay"]) or "arr_delay"
year_col        = resolve_one(raw, YEAR_CANDIDATES)   # optional
month_col       = resolve_one(raw, MONTH_CANDIDATES)  # optional

# ---- Cancellation detection ----
CANCEL_CANDIDATES = ["cancelled", "canceled", "arr_cancelled", "arr_cancel", "cancelled_flights", "cancellations"]
cancel_col = resolve_one(raw, CANCEL_CANDIDATES)

# Reason detection & mapping
reason_col = None
if "cancellation_code" in raw.columns:
    reason_col = "cancellation_code"
elif "cancellation_reason" in raw.columns:
    reason_col = "cancellation_reason"

CANCEL_REASON_MAP = {"A": "Carrier", "B": "Weather", "C": "NAS", "D": "Security"}

# Build unified cancel flag + reason text
if cancel_col and cancel_col in raw.columns:
    raw["_cancel_flag"] = pd.to_numeric(raw[cancel_col], errors="coerce").fillna(0).astype(int)
elif "cancellation_code" in raw.columns:
    raw["_cancel_flag"] = raw["cancellation_code"].notna().astype(int)
else:
    raw["_cancel_flag"] = 0  # no signal → all zeros, app still runs

if reason_col == "cancellation_code":
    raw["_cancel_reason"] = raw["cancellation_code"].map(CANCEL_REASON_MAP).fillna("Other/Unknown")
elif reason_col == "cancellation_reason":
    raw["_cancel_reason"] = raw["cancellation_reason"].fillna("Unknown")
else:
    raw["_cancel_reason"] = "Unknown"

# ---------- Basic type cleanup ----------
for col in [arr_flights_col, arr_del15_col, arr_delay_col, year_col, month_col]:
    if col in raw.columns:
        raw[col] = pd.to_numeric(raw[col], errors="coerce")

df = raw.dropna(subset=[carrier_col, airport_col, arr_flights_col, arr_del15_col, arr_delay_col]).copy()
df = df[df[arr_flights_col] > 0]

# ---------- Aggregate carrier × airport (with cancellations + reasons) ----------
grouped = (
    df.groupby([carrier_col, airport_col], as_index=False)
      .agg(
          total_flights   =(arr_flights_col, "sum"),
          delayed_flights =(arr_del15_col, "sum"),
          avg_delay       =(arr_delay_col, "mean"),
          canceled_flights=("_cancel_flag", "sum"),
      )
)
grouped["delay_probability"]  = grouped["delayed_flights"]  / grouped["total_flights"]
grouped["cancel_probability"] = grouped["canceled_flights"] / grouped["total_flights"]

# Most common cancel reason per carrier-airport
reason_summary = (
    raw.groupby([carrier_col, airport_col])["_cancel_reason"]
       .agg(lambda x: x.mode().iat[0] if not x.mode().empty else "Unknown")
       .reset_index(name="top_cancel_reason")
)
grouped = grouped.merge(reason_summary, on=[carrier_col, airport_col], how="left")

# ---------- UI: choose selection order (Carrier → Airport or Airport → Carrier) ----------
st.subheader("Pick your route")

# Helpers from aggregated table
def airports_for_carrier(c):
    return sorted(grouped.loc[grouped[carrier_col] == c, airport_col].unique())

def carriers_for_airport(a):
    return sorted(grouped.loc[grouped[airport_col] == a, carrier_col].unique())

all_carriers = sorted(grouped[carrier_col].unique())
all_airports = sorted(grouped[airport_col].unique())

order = st.radio("Select by…", ["Carrier → Airport", "Airport → Carrier"], horizontal=not MOBILE)

if order == "Carrier → Airport":
    cols = st.columns(1 if MOBILE else 2)

    # First picker (Carrier)
    with cols[0]:
        sel_carrier = st.selectbox(
            "Carrier",
            all_carriers,
            key="pick_carrier_first",
            help="Pick a carrier to see only airports that appear with it in your data."
        )

    # Second picker (Airport filtered by carrier)
    with (cols[0] if MOBILE else cols[1]):
        valid_airports = airports_for_carrier(sel_carrier)
        if not valid_airports:
            st.warning("No airports found for this carrier in your dataset.")
            st.stop()
        sel_airport = st.selectbox(
            "Airport (matches your carrier)",
            valid_airports,
            key="pick_airport_second"
        )

else:
    cols = st.columns(1 if MOBILE else 2)

    # First picker (Airport)
    with cols[0]:
        sel_airport = st.selectbox(
            "Airport",
            all_airports,
            key="pick_airport_first",
            help="Pick an airport to see only carriers that serve it in your data."
        )

    # Second picker (Carrier filtered by airport)
    with (cols[0] if MOBILE else cols[1]):
        valid_carriers = carriers_for_airport(sel_airport)
        if not valid_carriers:
            st.warning("No carriers found for this airport in your dataset.")
            st.stop()
        sel_carrier = st.selectbox(
            "Carrier (serves this airport)",
            valid_carriers,
            key="pick_carrier_second"
        )

# Use the selection to locate the row
sel = grouped[
    (grouped[carrier_col] == sel_carrier) &
    (grouped[airport_col] == sel_airport)
]
if sel.empty:
    st.warning("No data for that carrier–airport combination.")
    st.stop()


# ---------- Metrics ----------
prob_delay  = float(sel["delay_probability"].iloc[0]) * 100
prob_cancel = float(sel["cancel_probability"].iloc[0]) * 100
avg_d       = float(sel["avg_delay"].iloc[0])
tot         = int(sel["total_flights"].iloc[0])
dly         = int(sel["delayed_flights"].iloc[0])
cnl         = int(sel["canceled_flights"].iloc[0])
top_reason  = sel["top_cancel_reason"].iloc[0]

m1, m2, m3, m4 = st.columns(4)
m1.metric("Delay probability",  f"{prob_delay:.1f}%")
m2.metric("Cancel probability", f"{prob_cancel:.2f}%")
m3.metric("Avg delay (min)",    f"{avg_d:.1f}")
m4.metric("Flights analyzed",   f"{tot:,}")
st.caption(f"Delayed: {dly:,} / {tot:,}  •  Canceled: {cnl:,} / {tot:,}")
st.markdown(f"**Most common cancellation reason:** {top_reason}")

st.divider()

# ---------- Comparisons: delay & cancel ----------
# ---------- Helper for mobile-friendly x labels ----------
def shorten_labels(values, max_len_mobile=14, max_len_desktop=22):
    max_len = max_len_mobile if MOBILE else max_len_desktop
    out = []
    for v in values.astype(str).tolist():
        out.append(v if len(v) <= max_len else v[:max_len] + "…")
    return out

# ---------- Airports for <Carrier> — Delay probability (MOBILE SAFE) ----------
st.subheader(f"Airports for {sel_carrier} — Delay probability")
airports_df = peers_carrier.copy()
airports_df["_short"] = shorten_labels(airports_df[airport_col])

fig2 = px.bar(
    airports_df,
    x="_short",
    y="delay_probability",
    hover_data=["total_flights", "avg_delay"],
    labels={"_short": "Airport", "delay_probability": "Delay probability"},
    custom_data=[airports_df[airport_col]],  # keep full name for tooltip
)
fig2.update_traces(
    hovertemplate="<b>%{customdata[0]}</b><br>Delay probability=%{y:.0%}"
                  "<br>Flights=%{customdata[0]|string}<extra></extra>"
)
fig2.update_layout(
    xaxis_tickangle=-25 if MOBILE else -35,
    yaxis_tickformat=".0%",
    plot_bgcolor="white",
    height=UI["chart_h"] + (80 if MOBILE else 0),  # extra room for labels on phones
    margin=dict(l=10, r=10, t=40, b=10),
)
st.plotly_chart(fig2, use_container_width=True)

# ---------- Airports for <Carrier> — Cancel probability (MOBILE SAFE) ----------
st.subheader(f"Airports for {sel_carrier} — Cancel probability")
airports_cancel_df = peers_carrier_cancel.copy()
airports_cancel_df["_short"] = shorten_labels(airports_cancel_df[airport_col])

fig_c2 = px.bar(
    airports_cancel_df,
    x="_short",
    y="cancel_probability",
    hover_data=["total_flights", "canceled_flights", "top_cancel_reason"],
    labels={"_short": "Airport", "cancel_probability": "Cancel probability"},
    custom_data=[airports_cancel_df[airport_col]],  # full name for tooltip
)
fig_c2.update_traces(
    hovertemplate="<b>%{customdata[0]}</b><br>Cancel probability=%{y:.2%}"
                  "<br>Top reason=%{meta}<extra></extra>",
    meta=airports_cancel_df.get("top_cancel_reason", "—"),
)
fig_c2.update_layout(
    xaxis_tickangle=-25 if MOBILE else -35,
    yaxis_tickformat=".2%",
    plot_bgcolor="white",
    height=UI["chart_h"] + (80 if MOBILE else 0),
    margin=dict(l=10, r=10, t=40, b=10),
)
st.plotly_chart(fig_c2, use_container_width=True)

# ---------- Optional trends (if year/month exist) ----------
st.divider()
st.subheader("Trends (if year & month exist)")

if (year_col in df.columns if year_col else False) and (month_col in df.columns if month_col else False):
    base = df[(df[carrier_col] == sel_carrier) & (df[airport_col] == sel_airport)].dropna(
        subset=[year_col, month_col]
    ).copy()

    if not base.empty:
        base[year_col] = base[year_col].astype(int)
        base[month_col] = base[month_col].astype(int)

        trend = (
            base.groupby([year_col, month_col], as_index=False)
                .agg(
                    flights=(arr_flights_col, "sum"),
                    delayed=(arr_del15_col, "sum"),
                    avg_delay=(arr_delay_col, "mean"),
                    canceled=("_cancel_flag", "sum"),
                )
        )

        trend["delay_probability"] = trend["delayed"] / trend["flights"]
        trend["cancel_probability"] = trend["canceled"] / trend["flights"]
        trend["date"] = pd.to_datetime(
            trend[year_col].astype(str) + "-" + trend[month_col].astype(str) + "-01"
        )
        trend = trend.sort_values("date")

        # Create columns that work on mobile/desktop
        cols = st.columns(1 if MOBILE else 2)

        with cols[0]:
            st.subheader("Delay probability over time")
            figp = px.line(
                trend, x="date", y="delay_probability", markers=True,
                labels={"date": "Month", "delay_probability": "Delay probability"},
            )
            figp.update_layout(yaxis_tickformat=".0%", plot_bgcolor="white", height=UI["chart_h_trend"])
            st.plotly_chart(figp, use_container_width=True)

        with (cols[0] if MOBILE else cols[1]):
            st.subheader("Cancel probability over time")
            figc = px.line(
                trend, x="date", y="cancel_probability", markers=True,
                labels={"date": "Month", "cancel_probability": "Cancel probability"},
            )
            figc.update_layout(yaxis_tickformat=".2%", plot_bgcolor="white", height=UI["chart_h_trend"])
            st.plotly_chart(figc, use_container_width=True)

    else:
        st.caption("No monthly data for this carrier–airport combination.")
else:
    st.caption("Dataset doesn’t include year/month, so trends are hidden.")

# ---------- Live (only if user provides flight + key, or FAA IATA) ----------
st.divider()
st.subheader("Live tracking (optional)")
colA, colB, colC = st.columns([1,1,1])
with colA:
    flight_num = st.text_input("Flight number (e.g., DL123)", value="")
with colB:
    aviation_key = API_KEY  # from env/secrets if available
with colC:
    arrival_iata = st.text_input("Arrival IATA for FAA", value="").strip()

show_live = bool(flight_num and aviation_key)
if show_live or arrival_iata:
    faa = faa_airport_status(arrival_iata) if arrival_iata else None
    live_info = aviationstack_flight(flight_num, aviation_key) if show_live else None
    live_phase = (live_info.get("flight_status") if live_info else None) or ""

    # Try to extract a live cancel reason if the API provides one
    cancel_reason_live = None
    if isinstance(live_info, dict):
        # Some providers include a status or reason field; name varies by provider plan.
        cancel_reason_live = (
            live_info.get("status_reason")
            or (live_info.get("status") if isinstance(live_info.get("status"), str) else None)
            or (live_info.get("flight") or {}).get("status_text")
        )

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
            status = (live_info.get("flight_status") or "—").title()
            st.write(f"Status: **{status}**")
            st.write(f"Route: {dep} → {arr}")
            if status.lower() == "cancelled":
                st.error(f"This flight is **cancelled**" + (f" — Reason: {cancel_reason_live}" if cancel_reason_live else ""))
        else:
            st.write("—")

    with k3:
        baseline_delay  = float(sel["delay_probability"].iloc[0]) if not sel.empty else 0.0
        baseline_cancel = float(sel["cancel_probability"].iloc[0]) if not sel.empty else 0.0
        risk_delay  = risk_score_delay(baseline_delay, faa, live_phase)
        risk_cancel = risk_score_cancel(baseline_cancel, faa, live_phase)
        kA, kB = st.columns(2)
        with kA:
            st.metric("Estimated delay risk",  f"{100*risk_delay:.1f}%")
        with kB:
            st.metric("Estimated cancel risk", f"{100*risk_cancel:.1f}%")
        st.caption(f"Baselines — delay: {100*baseline_delay:.1f}% • cancel: {100*baseline_cancel:.2f}%")
