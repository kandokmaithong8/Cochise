import os
import math
from datetime import date, timedelta

import requests
import pandas as pd
import streamlit as st
import folium
import airportsdata
import streamlit.components.v1 as components


# ============================================================
# PAGE CONFIG
# ============================================================

st.set_page_config(
    page_title="Flight route + hotel recommender",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ============================================================
# SECRET LOADER
# ============================================================

def get_serpapi_key():
    key = None

    try:
        key = st.secrets.get("SERPAPI_KEY", None)
    except Exception:
        key = None

    if not key:
        key = os.environ.get("SERPAPI_KEY")

    if not key:
        st.error(
            "SERPAPI_KEY was not found. Please add this in Streamlit Community Cloud > App settings > Secrets:\n\n"
            'SERPAPI_KEY = "your_serpapi_key_here"'
        )

        with st.expander("Debug secret status"):
            try:
                st.write("Available secret keys:", list(st.secrets.keys()))
            except Exception as e:
                st.write("Could not read Streamlit secrets.")
                st.write(str(e))

        st.stop()

    return str(key).strip()


SERPAPI_KEY = get_serpapi_key()


# ============================================================
# CONSTANTS
# ============================================================

SERPAPI_URL = "https://serpapi.com/search.json"

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

POI_COLUMNS = ["name", "category", "lat", "lon", "cuisine", "tourism_type"]


# ============================================================
# AIRPORT DATA
# ============================================================

@st.cache_data(show_spinner=False)
def load_airports():
    raw = airportsdata.load("IATA")
    cleaned = {}

    for code, info in raw.items():
        lat = info.get("lat")
        lon = info.get("lon")

        if not code:
            continue

        if lat in (None, 0) or lon in (None, 0):
            continue

        cleaned[code] = {
            "name": info.get("name") or code,
            "city": info.get("city") or info.get("name") or code,
            "country": info.get("country") or "",
            "lat": lat,
            "lon": lon,
        }

    return cleaned


AIRPORTS = load_airports()
AIRPORT_CODES = sorted(AIRPORTS.keys())


def airport_label(code):
    a = AIRPORTS[code]
    return f"{code} | {a['city']}, {a['country']} | {a['name']}"


AIRPORT_SEARCH_TEXT = {
    code: f"{code} {a['city']} {a['country']} {a['name']}".lower()
    for code, a in AIRPORTS.items()
}


def smart_airport_picker(label, default_code, key):
    st.markdown(f"**{label}**")

    search_text = st.text_input(
        f"Search {label}",
        placeholder="Type airport code, city, country, or airport name",
        key=f"{key}_search",
        label_visibility="collapsed",
    )

    query = search_text.strip().lower()

    if query:
        tokens = query.split()
        filtered_codes = [
            code
            for code in AIRPORT_CODES
            if all(token in AIRPORT_SEARCH_TEXT[code] for token in tokens)
        ]
    else:
        filtered_codes = [default_code] + [
            code for code in AIRPORT_CODES if code != default_code
        ]

    filtered_codes = filtered_codes[:150]

    if not filtered_codes:
        st.warning("No airport found. Try airport code like BKK, NRT, LHR, JFK, or city name.")
        filtered_codes = [default_code]

    default_index = 0
    if default_code in filtered_codes:
        default_index = filtered_codes.index(default_code)

    selected_code = st.selectbox(
        label,
        filtered_codes,
        index=default_index,
        format_func=airport_label,
        key=f"{key}_select",
        label_visibility="collapsed",
    )

    return selected_code


# ============================================================
# GEO HELPERS
# ============================================================

def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0

    p1 = math.radians(float(lat1))
    p2 = math.radians(float(lat2))
    dlat = math.radians(float(lat2) - float(lat1))
    dlon = math.radians(float(lon2) - float(lon1))

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    )

    return r * 2 * math.asin(math.sqrt(a))


def great_circle_points(lat1, lon1, lat2, lon2, n=60):
    phi1, lam1, phi2, lam2 = map(math.radians, [lat1, lon1, lat2, lon2])

    d = 2 * math.asin(
        math.sqrt(
            math.sin((phi2 - phi1) / 2) ** 2
            + math.cos(phi1) * math.cos(phi2) * math.sin((lam2 - lam1) / 2) ** 2
        )
    )

    if d == 0:
        return [(lat1, lon1)]

    points = []

    for i in range(n + 1):
        f = i / n

        a = math.sin((1 - f) * d) / math.sin(d)
        b = math.sin(f * d) / math.sin(d)

        x = a * math.cos(phi1) * math.cos(lam1) + b * math.cos(phi2) * math.cos(lam2)
        y = a * math.cos(phi1) * math.sin(lam1) + b * math.cos(phi2) * math.sin(lam2)
        z = a * math.sin(phi1) + b * math.sin(phi2)

        lat = math.degrees(math.atan2(z, math.sqrt(x ** 2 + y ** 2)))
        lon = math.degrees(math.atan2(y, x))

        points.append((lat, lon))

    return points


def norm_score(value, lo, hi, invert=False):
    if pd.isna(value):
        return 0.0

    if hi == lo:
        return 100.0

    t = (value - lo) / (hi - lo)
    t = max(0.0, min(1.0, t))

    if invert:
        return round((1 - t) * 100, 1)

    return round(t * 100, 1)


def parse_hour(time_str):
    try:
        return int(str(time_str).split(" ")[1].split(":")[0])
    except Exception:
        return None


# ============================================================
# API CALLS
# ============================================================

@st.cache_data(ttl=3600, show_spinner=False)
def call_serpapi(params, api_key):
    p = dict(params)
    p["api_key"] = api_key

    response = requests.get(
        SERPAPI_URL,
        params=p,
        timeout=60,
    )

    try:
        data = response.json()
    except Exception as exc:
        raise RuntimeError(
            f"SerpAPI returned a non-JSON response. HTTP {response.status_code}"
        ) from exc

    if response.status_code != 200:
        raise RuntimeError(f"SerpAPI HTTP error: {response.status_code}")

    if "error" in data:
        raise RuntimeError(str(data.get("error")))

    return data


@st.cache_data(ttl=3600, show_spinner=False)
def overpass_nearby(lat, lon, radius_m=3000):
    try:
        lat = float(lat)
        lon = float(lon)
        radius_m = int(radius_m)
    except Exception:
        return pd.DataFrame(columns=POI_COLUMNS)

    if pd.isna(lat) or pd.isna(lon):
        return pd.DataFrame(columns=POI_COLUMNS)

    radius_m = max(500, min(radius_m, 8000))

    query = f"""
    [out:json][timeout:20];
    (
      nwraround:{radius_m},{lat},{lon};
      nwraround:{radius_m},{lat},{lon};
    );
    out center tags 100;
    """

    headers = {
        "User-Agent": "flight-hotel-recommender-streamlit/1.0"
    }

    for url in OVERPASS_URLS:
        try:
            response = requests.post(
                url,
                data={"data": query},
                headers=headers,
                timeout=25,
            )

            if response.status_code in (429, 500, 502, 503, 504):
                continue

            response.raise_for_status()
            data = response.json()

            rows = []

            for element in data.get("elements", []):
                tags = element.get("tags", {}) or {}
                name = tags.get("name")

                if not name:
                    continue

                center = element.get("center", {}) or {}

                poi_lat = element.get("lat", center.get("lat"))
                poi_lon = element.get("lon", center.get("lon"))

                if poi_lat is None or poi_lon is None:
                    continue

                category = (
                    "restaurant"
                    if tags.get("amenity") in ("restaurant", "cafe", "bar")
                    else "attraction"
                )

                rows.append(
                    {
                        "name": name,
                        "category": category,
                        "lat": poi_lat,
                        "lon": poi_lon,
                        "cuisine": tags.get("cuisine"),
                        "tourism_type": tags.get("tourism"),
                    }
                )

            df = pd.DataFrame(rows)

            if df.empty:
                return pd.DataFrame(columns=POI_COLUMNS)

            for col in POI_COLUMNS:
                if col not in df.columns:
                    df[col] = None

            df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
            df["lon"] = pd.to_numeric(df["lon"], errors="coerce")

            df = df.dropna(subset=["lat", "lon"])
            df = df.drop_duplicates(subset=["name", "lat", "lon"])

            return df[POI_COLUMNS].reset_index(drop=True)

        except Exception:
            continue

    return pd.DataFrame(columns=POI_COLUMNS)


def poi_count_near(df_pois, lat, lon, km=2.0):
    if df_pois.empty:
        return 0

    valid = df_pois.dropna(subset=["lat", "lon"])

    if valid.empty:
        return 0

    distances = valid.apply(
        lambda row: haversine_km(lat, lon, row["lat"], row["lon"]),
        axis=1,
    )

    return int((distances <= km).sum())


# ============================================================
# SEARCH LOGIC
# ============================================================

def run_search(api_key, dep_iata, arr_iata, outbound_date, return_date, currency, radius_m):
    dep = AIRPORTS[dep_iata]
    arr = AIRPORTS[arr_iata]

    flight_json = call_serpapi(
        {
            "engine": "google_flights",
            "departure_id": dep_iata,
            "arrival_id": arr_iata,
            "outbound_date": str(outbound_date),
            "return_date": str(return_date),
            "type": "1",
            "currency": currency,
            "hl": "en",
            "adults": 1,
            "travel_class": 1,
        },
        api_key,
    )

    all_flights = (
        flight_json.get("best_flights") or []
    ) + (
        flight_json.get("other_flights") or []
    )

    flight_rows = []

    for flight in all_flights:
        legs = flight.get("flights", [])

        if not legs:
            continue

        first_leg = legs[0]
        last_leg = legs[-1]

        flight_rows.append(
            {
                "price": flight.get("price"),
                "duration_min": flight.get("total_duration"),
                "airline": first_leg.get("airline"),
                "stops": len(legs) - 1,
                "departure_time": first_leg.get("departure_airport", {}).get("time"),
                "arrival_time": last_leg.get("arrival_airport", {}).get("time"),
            }
        )

    df_flights = pd.DataFrame(flight_rows)

    if df_flights.empty:
        raise RuntimeError("No usable flights returned for this route and date pair.")

    df_flights = df_flights.dropna(subset=["price"])
    df_flights = df_flights.sort_values("price").reset_index(drop=True)

    if df_flights.empty:
        raise RuntimeError("Flights were returned, but none had price data.")

    hotel_json = call_serpapi(
        {
            "engine": "google_hotels",
            "q": arr["city"],
            "check_in_date": str(outbound_date),
            "check_out_date": str(return_date),
            "currency": currency,
            "hl": "en",
        },
        api_key,
    )

    hotel_rows = []

    for hotel in hotel_json.get("properties", []):
        gps = hotel.get("gps_coordinates") or {}

        if "latitude" not in gps or "longitude" not in gps:
            continue

        price = (hotel.get("rate_per_night") or {}).get("extracted_lowest")

        hotel_rows.append(
            {
                "name": hotel.get("name"),
                "price": price,
                "rating": hotel.get("overall_rating"),
                "reviews": hotel.get("reviews"),
                "lat": gps["latitude"],
                "lon": gps["longitude"],
                "link": hotel.get("link"),
            }
        )

    df_hotels = pd.DataFrame(hotel_rows)

    if df_hotels.empty:
        raise RuntimeError("No hotels with location data were returned for this city.")

    df_hotels = df_hotels.dropna(subset=["price", "rating", "lat", "lon"])
    df_hotels = df_hotels.head(12)

    if df_hotels.empty:
        raise RuntimeError("No hotels with complete price, rating, and location data were returned.")

    city_center_lat = df_hotels["lat"].mean()
    city_center_lon = df_hotels["lon"].mean()

    df_pois = overpass_nearby(
        city_center_lat,
        city_center_lon,
        radius_m,
    )

    df_hotels["airport_dist_km"] = df_hotels.apply(
        lambda row: round(
            haversine_km(
                row["lat"],
                row["lon"],
                arr["lat"],
                arr["lon"],
            ),
            1,
        ),
        axis=1,
    )

    df_hotels["poi_count"] = df_hotels.apply(
        lambda row: poi_count_near(
            df_pois,
            row["lat"],
            row["lon"],
        ),
        axis=1,
    )

    return {
        "dep_iata": dep_iata,
        "arr_iata": arr_iata,
        "dep": dep,
        "arr": arr,
        "currency": currency,
        "df_flights": df_flights,
        "df_hotels": df_hotels,
        "df_pois": df_pois,
    }


def rank_hotels(df_hotels, weights):
    df = df_hotels.copy()

    wsum = sum(weights.values()) or 1

    df["price_score"] = df["price"].apply(
        lambda value: norm_score(
            value,
            df["price"].min(),
            df["price"].max(),
            invert=True,
        )
    )

    df["rating_score"] = (df["rating"] / 5 * 100).round(1)

    df["airport_score"] = df["airport_dist_km"].apply(
        lambda value: norm_score(
            value,
            df["airport_dist_km"].min(),
            df["airport_dist_km"].max(),
            invert=True,
        )
    )

    df["poi_score"] = df["poi_count"].apply(
        lambda value: norm_score(
            value,
            df["poi_count"].min(),
            df["poi_count"].max(),
            invert=False,
        )
    )

    df["composite"] = (
        df["price_score"] * weights["price"]
        + df["rating_score"] * weights["rating"]
        + df["airport_score"] * weights["airport"]
        + df["poi_score"] * weights["poi"]
    ) / wsum

    df = df.sort_values("composite", ascending=False).reset_index(drop=True)
    df.insert(0, "rank", df.index + 1)

    return df


# ============================================================
# DESIGN TOKENS
# ============================================================

BG = "#0A101E"
PANEL = "#111A2E"
PANEL_ALT = "#16213A"
BORDER = "#22314F"

AMBER = "#F5A623"
AMBER_SOFT = "#3A2C12"
CYAN = "#4FD1C5"
ROSE = "#E2637A"

TEXT = "#E7ECF3"
TEXT_MUTED = "#8996AC"
TEXT_DIM = "#5C6882"


# ============================================================
# CSS
# ============================================================

st.markdown(
    f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap');

html, body, [class*="css"] {{
    font-family: 'Inter', sans-serif;
}}

.stApp {{
    background: {BG};
    color: {TEXT};
}}

h1, h2, h3, h4 {{
    font-family: 'Space Grotesk', sans-serif !important;
    font-weight: 600 !important;
}}

[data-testid="stSidebar"] {{
    background: {PANEL};
    border-right: 1px solid {BORDER};
}}

[data-testid="stSidebar"] * {{
    color: {TEXT};
}}

[data-testid="stHeader"] {{
    background: transparent;
}}

.fhp-eyebrow {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px;
    letter-spacing: 3px;
    color: {AMBER};
    margin-bottom: 6px;
    text-transform: uppercase;
}}

.fhp-title {{
    font-family: 'Space Grotesk', sans-serif;
    font-weight: 700;
    font-size: 34px;
    margin: 0 0 6px;
    color: {TEXT};
}}

.fhp-subtitle {{
    color: {TEXT_MUTED};
    font-size: 14px;
    max-width: 720px;
    line-height: 1.5;
    margin-bottom: 8px;
}}

.stButton > button {{
    border-radius: 8px;
    border: 1px solid {BORDER};
    background: {PANEL_ALT};
    color: {TEXT};
    font-family: 'Inter', sans-serif;
    font-weight: 500;
}}

.stButton > button[kind="primary"] {{
    background: {AMBER};
    color: #1A1206;
    border: none;
    font-weight: 600;
}}

.stButton > button[kind="primary"]:hover {{
    background: #ffb84d;
    color: #1A1206;
}}

.stDownloadButton > button {{
    border-radius: 8px;
    border: 1px solid {BORDER};
    background: {PANEL_ALT};
    color: {TEXT};
}}

[data-testid="stTextInput"] input,
[data-testid="stDateInput"] input,
[data-baseweb="select"] > div {{
    background: {PANEL_ALT} !important;
    border: 1px solid {BORDER} !important;
    color: {TEXT} !important;
    border-radius: 8px !important;
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 13px !important;
}}

[data-testid="stSlider"] [role="slider"] {{
    background-color: {AMBER} !important;
    border-color: {AMBER} !important;
}}

[data-testid="stSlider"] div[data-baseweb="slider"] > div > div {{
    background: {AMBER} !important;
}}

[data-testid="stMetric"] {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 12px 16px;
}}

[data-testid="stMetricLabel"] {{
    color: {TEXT_MUTED} !important;
    font-size: 11px !important;
    text-transform: uppercase;
    letter-spacing: 1px;
}}

[data-testid="stMetricValue"] {{
    color: {TEXT} !important;
    font-family: 'JetBrains Mono', monospace !important;
}}

[data-testid="stDataFrame"] {{
    border: 1px solid {BORDER};
    border-radius: 10px;
    overflow: hidden;
}}

[data-testid="stExpander"] {{
    border: 1px solid {BORDER};
    border-radius: 10px;
    background: {PANEL};
}}

hr {{
    border-color: {BORDER};
}}

.fhp-card {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 12px;
    padding: 16px;
    margin-bottom: 10px;
}}

.fhp-card-top1 {{
    border-color: {AMBER};
}}

.fhp-rank {{
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    border-radius: 8px;
    font-family: 'JetBrains Mono', monospace;
    font-weight: 600;
    font-size: 13px;
    margin-right: 12px;
    flex-shrink: 0;
}}

.fhp-score {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 20px;
    font-weight: 600;
}}

.fhp-bar-track {{
    height: 4px;
    background: {BORDER};
    border-radius: 2px;
    overflow: hidden;
}}

.fhp-bar-fill {{
    height: 100%;
    background: {CYAN};
}}

.fhp-tag {{
    display: inline-flex;
    align-items: center;
    gap: 4px;
    font-size: 11px;
    color: {TEXT_MUTED};
    font-family: 'JetBrains Mono', monospace;
}}
</style>
""",
    unsafe_allow_html=True,
)


# ============================================================
# HEADER
# ============================================================

st.markdown(
    '<div class="fhp-eyebrow">FLIGHT PATH RECOMMENDER</div>',
    unsafe_allow_html=True,
)

st.markdown(
    '<div class="fhp-title">Flight route + hotel recommender</div>',
    unsafe_allow_html=True,
)

st.markdown(
    '<div class="fhp-subtitle">'
    'Real flight and hotel data via SerpAPI Google Flights and Google Hotels. '
    'Nearby attractions and restaurants via OpenStreetMap Overpass. '
    'Search any airport by code, city, country, or airport name.'
    '</div>',
    unsafe_allow_html=True,
)


# ============================================================
# SESSION STATE
# ============================================================

if "raw" not in st.session_state:
    st.session_state.raw = None

if "search_error" not in st.session_state:
    st.session_state.search_error = None


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:
    st.markdown(
        '<div class="fhp-eyebrow" style="margin-top:-8px;">SEARCH</div>',
        unsafe_allow_html=True,
    )

    st.success("SerpAPI key loaded from Streamlit Secrets")

    default_dep = "BKK" if "BKK" in AIRPORTS else AIRPORT_CODES[0]
    default_arr = "NRT" if "NRT" in AIRPORTS else AIRPORT_CODES[1]

    dep_iata = smart_airport_picker(
        label="Departure airport",
        default_code=default_dep,
        key="departure",
    )

    arr_iata = smart_airport_picker(
        label="Arrival airport",
        default_code=default_arr,
        key="arrival",
    )

    default_outbound = date.today() + timedelta(days=30)
    default_return = default_outbound + timedelta(days=5)

    col_a, col_b = st.columns(2)

    outbound_date = col_a.date_input(
        "Outbound date",
        value=default_outbound,
    )

    return_date = col_b.date_input(
        "Return date",
        value=default_return,
    )

    currency = st.selectbox(
        "Currency",
        ["USD", "THB", "EUR", "GBP", "JPY"],
        index=1,
    )

    st.markdown(
        '<div class="fhp-eyebrow">RANKING WEIGHTS</div>',
        unsafe_allow_html=True,
    )

    st.caption("These sliders recalculate the hotel ranking without another API call.")

    w_price = st.slider("Price", 0, 100, 25, 5)
    w_rating = st.slider("Guest rating", 0, 100, 25, 5)
    w_airport = st.slider("Near airport", 0, 100, 25, 5)
    w_poi = st.slider("Near attractions and restaurants", 0, 100, 25, 5)

    radius_m = st.slider(
        "POI search radius in meters",
        1000,
        8000,
        3000,
        500,
    )

    search_clicked = st.button(
        "Search",
        type="primary",
        use_container_width=True,
    )

    reset_clicked = st.button(
        "Reset",
        use_container_width=True,
    )


# ============================================================
# BUTTON ACTIONS
# ============================================================

if reset_clicked:
    st.session_state.raw = None
    st.session_state.search_error = None
    st.rerun()


if search_clicked:
    if dep_iata == arr_iata:
        st.session_state.search_error = "Departure and arrival airports must be different."
        st.session_state.raw = None

    elif return_date <= outbound_date:
        st.session_state.search_error = "Return date must be after outbound date."
        st.session_state.raw = None

    else:
        st.session_state.search_error = None

        with st.spinner("Searching flights, hotels, and nearby places..."):
            try:
                st.session_state.raw = run_search(
                    SERPAPI_KEY,
                    dep_iata,
                    arr_iata,
                    outbound_date,
                    return_date,
                    currency,
                    radius_m,
                )

            except Exception as e:
                st.session_state.search_error = str(e)
                st.session_state.raw = None


if st.session_state.search_error:
    st.error(st.session_state.search_error)


if st.session_state.raw is None:
    if not st.session_state.search_error:
        st.info(
            "Search airport by code, city, country, or airport name in the sidebar, then click Search. "
            "The API key is loaded automatically from Streamlit Secrets."
        )

    st.stop()


# ============================================================
# RESULTS
# ============================================================

raw = st.session_state.raw

dep = raw["dep"]
arr = raw["arr"]

dep_iata = raw["dep_iata"]
arr_iata = raw["arr_iata"]
currency = raw["currency"]

df_flights = raw["df_flights"]
df_pois = raw["df_pois"]

weights = {
    "price": w_price,
    "rating": w_rating,
    "airport": w_airport,
    "poi": w_poi,
}

df_hotels = rank_hotels(raw["df_hotels"], weights)

cheapest = df_flights.iloc[0]

arrival_hour = parse_hour(cheapest["arrival_time"])
is_night = arrival_hour is not None and (arrival_hour >= 22 or arrival_hour < 6)

if pd.notna(cheapest["duration_min"]):
    duration_str = (
        f"{int(cheapest['duration_min'] // 60)}h "
        f"{int(cheapest['duration_min'] % 60)}m"
    )
else:
    duration_str = "n/a"


# ============================================================
# ROUTE SUMMARY
# ============================================================

st.markdown(
    '<div class="fhp-eyebrow" style="margin-top:28px;">ROUTE</div>',
    unsafe_allow_html=True,
)

st.markdown(
    f'<div class="fhp-title" style="font-size:22px;">{dep_iata} to {arr_iata}</div>',
    unsafe_allow_html=True,
)

stat_cols = st.columns(4)

stats = [
    ("Cheapest price", f"{cheapest['price']} {currency}"),
    ("Duration", duration_str),
    ("Arrival, local", cheapest["arrival_time"] or "n/a"),
    ("Arrival window", "Night" if is_night else "Day"),
]

for col, (label, value) in zip(stat_cols, stats):
    accent = AMBER if label == "Arrival window" else TEXT

    col.markdown(
        f"""
        <div style="background:{PANEL};border:1px solid {BORDER};border-radius:10px;padding:12px 16px;">
            <div style="font-size:11px;color:{TEXT_MUTED};text-transform:uppercase;letter-spacing:1px;margin-bottom:4px;">
                {label}
            </div>
            <div style="font-family:'JetBrains Mono',monospace;font-size:18px;color:{accent};">
                {value}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


with st.expander(f"All {len(df_flights)} flight options"):
    st.dataframe(
        df_flights,
        use_container_width=True,
        hide_index=True,
    )


# ============================================================
# HOTELS
# ============================================================

st.markdown(
    '<div class="fhp-eyebrow" style="margin-top:32px;">HOTELS</div>',
    unsafe_allow_html=True,
)

st.markdown(
    f'<div class="fhp-title" style="font-size:22px;">Recommended in {arr["city"]}</div>',
    unsafe_allow_html=True,
)

st.markdown(
    '<div class="fhp-subtitle">'
    'Score blends price, guest rating, distance to the arrival airport, '
    'and nearby attractions or restaurants. Adjust the sliders in the sidebar.'
    '</div>',
    unsafe_allow_html=True,
)

hotel_display = (
    df_hotels[
        [
            "rank",
            "name",
            "price",
            "rating",
            "reviews",
            "airport_dist_km",
            "poi_count",
            "composite",
        ]
    ]
    .rename(
        columns={
            "airport_dist_km": "km to airport",
            "poi_count": "nearby spots",
            "composite": "score",
        }
    )
    .round({"score": 1})
)

cards_html = []

for _, hotel in df_hotels.iterrows():
    is_top = hotel["rank"] == 1

    rank_bg = AMBER_SOFT if is_top else PANEL_ALT
    rank_color = AMBER if is_top else TEXT_MUTED

    bars = [
        ("Price", hotel["price_score"]),
        ("Rating", hotel["rating_score"]),
        ("Airport", hotel["airport_score"]),
        ("Nearby", hotel["poi_score"]),
    ]

    bars_html = ""

    for label, score in bars:
        bars_html += f"""
        <div style="flex:1;min-width:70px;">
            <div class="fhp-bar-track">
                <div class="fhp-bar-fill" style="width:{score}%;"></div>
            </div>
            <div style="font-size:10px;color:{TEXT_DIM};margin-top:3px;">
                {label}
            </div>
        </div>
        """

    rating_value = hotel["rating"] if pd.notna(hotel["rating"]) else 0
    full_stars = max(0, min(5, round(rating_value)))
    stars = "★" * full_stars + "☆" * (5 - full_stars)

    reviews_value = int(hotel["reviews"]) if pd.notna(hotel["reviews"]) else 0

    cards_html.append(
        f"""
        <div class="fhp-card{' fhp-card-top1' if is_top else ''}">
            <div style="display:flex;gap:14px;flex-wrap:wrap;">
                <div class="fhp-rank" style="background:{rank_bg};color:{rank_color};">
                    {hotel['rank']}
                </div>

                <div style="flex:1;min-width:220px;">
                    <div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px;">
                        <div style="font-family:'Space Grotesk',sans-serif;font-weight:600;font-size:15px;">
                            {hotel['name']}
                        </div>

                        <div style="font-family:'JetBrains Mono',monospace;font-size:15px;color:{AMBER};">
                            {hotel['price']} {currency}
                            <span style="color:{TEXT_MUTED};font-size:11px;"> /night</span>
                        </div>
                    </div>

                    <div style="display:flex;gap:16px;flex-wrap:wrap;margin:6px 0 10px;">
                        <span class="fhp-tag" style="color:{AMBER};">
                            {stars}
                            <span style="color:{TEXT_MUTED};">
                                &nbsp;{hotel['rating']} ({reviews_value})
                            </span>
                        </span>

                        <span class="fhp-tag">
                            Airport {hotel['airport_dist_km']} km
                        </span>

                        <span class="fhp-tag">
                            Nearby {hotel['poi_count']} spots
                        </span>
                    </div>

                    <div style="display:flex;gap:10px;flex-wrap:wrap;">
                        {bars_html}
                    </div>
                </div>

                <div class="fhp-score" style="align-self:center;color:{AMBER if is_top else TEXT};min-width:46px;text-align:right;">
                    {hotel['composite']:.0f}
                </div>
            </div>
        </div>
        """
    )

st.markdown(
    "".join(cards_html),
    unsafe_allow_html=True,
)


# ============================================================
# MAP
# ============================================================

st.markdown(
    '<div class="fhp-eyebrow" style="margin-top:28px;">MAP</div>',
    unsafe_allow_html=True,
)

st.markdown(
    '<div class="fhp-title" style="font-size:22px;">Route and destination</div>',
    unsafe_allow_html=True,
)

m = folium.Map(
    location=[
        (dep["lat"] + arr["lat"]) / 2,
        (dep["lon"] + arr["lon"]) / 2,
    ],
    zoom_start=3,
    tiles="cartodbpositron",
)

route = great_circle_points(
    dep["lat"],
    dep["lon"],
    arr["lat"],
    arr["lon"],
)

folium.PolyLine(
    route,
    color=AMBER,
    weight=3,
    opacity=0.85,
    dash_array="6,6",
).add_to(m)

folium.Marker(
    [dep["lat"], dep["lon"]],
    tooltip=f"{dep_iata} - {dep['name']}",
    icon=folium.Icon(color="blue", icon="plane", prefix="fa"),
).add_to(m)

folium.Marker(
    [arr["lat"], arr["lon"]],
    tooltip=f"{arr_iata} - {arr['name']}",
    icon=folium.Icon(color="blue", icon="plane", prefix="fa"),
).add_to(m)

for _, hotel in df_hotels.iterrows():
    marker_color = "orange" if hotel["rank"] == 1 else "cadetblue"

    folium.Marker(
        [hotel["lat"], hotel["lon"]],
        tooltip=(
            f"#{hotel['rank']} {hotel['name']} | "
            f"{hotel['price']} {currency}, score {hotel['composite']:.0f}"
        ),
        icon=folium.Icon(color=marker_color, icon="bed", prefix="fa"),
    ).add_to(m)

if not df_pois.empty:
    for _, poi in df_pois.dropna(subset=["lat", "lon"]).head(60).iterrows():
        poi_color = CYAN if poi["category"] == "attraction" else ROSE

        folium.CircleMarker(
            [poi["lat"], poi["lon"]],
            radius=4,
            color=poi_color,
            fill=True,
            fill_opacity=0.8,
            tooltip=f"{poi['name']} ({poi['category']})",
        ).add_to(m)

bounds = (
    [[dep["lat"], dep["lon"]], [arr["lat"], arr["lon"]]]
    + df_hotels[["lat", "lon"]].values.tolist()
)

m.fit_bounds(bounds)

st.markdown(
    f'<div style="border:1px solid {BORDER};border-radius:12px;overflow:hidden;">',
    unsafe_allow_html=True,
)

components.html(
    m._repr_html_(),
    height=560,
    scrolling=False,
)

st.markdown(
    "</div>",
    unsafe_allow_html=True,
)


# ============================================================
# NEARBY
# ============================================================

st.markdown(
    '<div class="fhp-eyebrow" style="margin-top:32px;">NEARBY</div>',
    unsafe_allow_html=True,
)

st.markdown(
    f'<div class="fhp-title" style="font-size:22px;">In {arr["city"]}</div>',
    unsafe_allow_html=True,
)

if df_pois.empty:
    st.caption(
        "No attractions or restaurants found from Overpass. "
        "This can happen if the public Overpass server is busy, blocked, "
        "or there are no OpenStreetMap results in the selected radius."
    )

else:
    for col in POI_COLUMNS:
        if col not in df_pois.columns:
            df_pois[col] = None

    tab_all, tab_attr, tab_food = st.tabs(
        ["All", "Attractions", "Restaurants"]
    )

    for tab, category_filter in [
        (tab_all, None),
        (tab_attr, "attraction"),
        (tab_food, "restaurant"),
    ]:
        with tab:
            shown = (
                df_pois
                if category_filter is None
                else df_pois[df_pois["category"] == category_filter]
            )

            if shown.empty:
                st.caption("No records found for this category.")
                continue

            poi_cards = []

            for _, poi in shown.iterrows():
                is_food = poi["category"] == "restaurant"
                icon = "Food" if is_food else "Place"
                color = ROSE if is_food else CYAN

                if is_food and pd.notna(poi["cuisine"]):
                    meta = poi["cuisine"]
                elif pd.notna(poi["tourism_type"]):
                    meta = poi["tourism_type"]
                else:
                    meta = poi["category"]

                poi_cards.append(
                    f"""
                    <div style="background:{PANEL};border:1px solid {BORDER};border-radius:10px;padding:12px 14px;">
                        <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">
                            <span style="color:{color};font-size:11px;font-family:'JetBrains Mono',monospace;">
                                {icon}
                            </span>
                            <span style="font-family:'Space Grotesk',sans-serif;font-weight:600;font-size:13px;">
                                {poi['name']}
                            </span>
                        </div>
                        <div class="fhp-tag">{meta or ''}</div>
                    </div>
                    """
                )

            cols = st.columns(3)

            for i, card in enumerate(poi_cards):
                cols[i % 3].markdown(
                    card,
                    unsafe_allow_html=True,
                )

                cols[i % 3].markdown(
                    "<div style='height:8px;'></div>",
                    unsafe_allow_html=True,
                )


# ============================================================
# EXPORT
# ============================================================

st.markdown(
    '<div class="fhp-eyebrow" style="margin-top:32px;">EXPORT</div>',
    unsafe_allow_html=True,
)

csv_data = hotel_display.to_csv(index=False).encode("utf-8")

st.download_button(
    label="Download hotel ranking as CSV",
    data=csv_data,
    file_name=f"hotel_ranking_{dep_iata}_to_{arr_iata}.csv",
    mime="text/csv",
    use_container_width=True,
)

st.markdown(
    f'<div style="color:{TEXT_DIM};font-size:12px;margin-top:12px;">'
    'Data: SerpAPI Google Flights, SerpAPI Google Hotels, '
    'OpenStreetMap contributors via Overpass API, '
    'and airport data via OurAirports public domain.'
    '</div>',
    unsafe_allow_html=True,
)
