✈️ Live Flight Delay & Cancellation Tracker

A real-time flight delay and cancellation dashboard built with Streamlit, powered by live data from AviationStack
 (or optionally, the free OpenSky Network
 API).
The app provides an interactive interface for tracking airline performance, viewing flight statuses, and estimating the probability of flight delays and cancellations.

🚀 Features

Live flight data (from AviationStack or OpenSky Network)

Filter by airline, airport, and status (e.g., scheduled, active, landed, cancelled)

Dynamic visualizations:

Pie charts for airline and delay distribution

Bar charts for route-level delay rates

KPIs showing counts of scheduled, delayed, and cancelled flights

Tabs for easy navigation between:

All flights

Scheduled, Active, Landed, Cancelled, etc.

Delays tab for full delay analytics

Automatic API key fallback (if your primary AviationStack key exceeds quota)

Dark mode optimized UI

🧩 Data Sources
Option 1: AviationStack (default)

Provides structured global flight data with fields like:

Airline IATA/ICAO

Origin and destination airports

Scheduled and actual departure/arrival times

Delay minutes and cancellation flags

⚠️ Free plans are limited to 500 requests/month — consider adding a backup key.

Option 2: OpenSky Network (free fallback)

Fetches live aircraft telemetry (position, speed, callsign).
Does not include official flight delay/cancellation data but can be used to infer traffic around airports.