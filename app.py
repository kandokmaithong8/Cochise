import os
import math
from io import BytesIO
from datetime import date, timedelta

import requests
import pandas as pd
import streamlit as st
import folium
import airportsdata
import pycountry
import streamlit.components.v1 as components
from streamlit_searchbox import st_searchbox


# ============================================================
# PAGE CONFIG
# ============================================================

st.set_page_config(
    page_title="Flight route + hotel recommender",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ============================================================
# HTML RENDER HELPER
# Streamlit's Markdown renderer treats any line that starts
# with 4+ leading spaces as a code block, per CommonMark rules.
# Since HTML snippets are built inside indented Python blocks
# (loops, functions), the f-strings inherit that indentation and
# get rendered as literal text instead of HTML. This helper strips
# leading/trailing whitespace from every line before handing it to
# st.markdown, so Python source indentation never leaks into the
# rendered output.
# ============================================================

def render_html(html: str):
    cleaned_lines = [line.strip() for line in html.split("\n")]
    cleaned = "\n".join(cleaned_lines).strip()
    st.markdown(cleaned, unsafe_allow_html=True)


# ============================================================
# AUTO LOAD SERPAPI KEY FROM STREAMLIT SECRETS
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
        st.error("SERPAPI_KEY was not found in Streamlit Secrets.")
        st.code('SERPAPI_KEY = "your_serpapi_key_here"', language="toml")

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

POI_COLUMNS = [
    "name",
    "local_name",
    "category",
    "lat",
    "lon",
    "cuisine",
    "tourism_type",
    "notable",
    "dist_from_center_km",
]

APP_SHARE_URL = "https://cochise-kgrf2xcmc842zpgng9j9ct.streamlit.app/"


# ============================================================
# AIRPORTS
# ============================================================

@st.cache_data(show_spinner=False)
def load_airports():
    raw = airportsdata.load("IATA")
    cleaned = {}

    for code, info in raw.items():
        lat = info.get("lat")
        lon = info.get("lon")

        if not code or lat in (None, 0) or lon in (None, 0):
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


def airport_label(code):
    a = AIRPORTS[code]
    return f"{code} - {a['city']}, {a['country']} ({a['name']})"


# ============================================================
# COUNTRY NAME + FLAG HELPERS FOR ALL COUNTRIES
# ============================================================

@st.cache_data(show_spinner=False)
def build_country_display_name_map():
    mapping = {}

    for country in pycountry.countries:
        alpha_2 = getattr(country, "alpha_2", "") or ""

        if not alpha_2:
            continue

        display_name = (
            getattr(country, "common_name", None)
            or getattr(country, "name", None)
            or alpha_2
        )

        mapping[alpha_2.upper()] = display_name

    return mapping


COUNTRY_DISPLAY_NAME_MAP = build_country_display_name_map()


def country_display_name(country_value):
    if not country_value:
        return ""

    code = str(country_value).strip().upper()
    return COUNTRY_DISPLAY_NAME_MAP.get(code, code)


def country_flag_emoji(country_value):
    code = str(country_value or "").strip().upper()

    if len(code) != 2 or not code.isalpha():
        return ""

    try:
        return "".join(chr(0x1F1E6 + ord(ch) - ord("A")) for ch in code)
    except Exception:
        return ""


# ============================================================
# SMART AIRPORT SEARCH
#
# Streamlit's own st.selectbox has a known, documented issue: its
# built-in "fuzzy" client-side filter does not reliably behave as
# exact-match-first (GitHub issue streamlit/streamlit #16003 - the
# frontend search re-filters Streamlit's own fuzzy results with an
# additional substring/fuzzy pass, so typing an exact IATA code can
# still surface many loosely related options instead of only that
# one airport). Since the desired behavior here is fully precise
# and deterministic - typing "BKK" must return ONLY Bangkok's BKK,
# while typing "Thailand" or "Japan" must return EVERY airport in
# that country - we do not rely on the opaque built-in filter at
# all. Instead we use the `streamlit-searchbox` component, which
# calls our own Python search function on every keystroke (live,
# no Enter needed) and renders exactly the results we return, in
# the order we return them.
#
# pip install streamlit-searchbox
# ============================================================

@st.cache_data(show_spinner=False)
def build_country_search_terms_map():
    """
    Full set of searchable terms per ISO alpha-2 country code:
    alpha-2, alpha-3, short name, official name, common name.
    Covers every country pycountry knows about.
    """
    mapping = {}

    for country in pycountry.countries:
        alpha_2 = getattr(country, "alpha_2", "") or ""

        if not alpha_2:
            continue

        terms = set()

        for value in [
            alpha_2,
            getattr(country, "alpha_3", "") or "",
            getattr(country, "name", "") or "",
            getattr(country, "official_name", "") or "",
            getattr(country, "common_name", "") or "",
        ]:
            if value:
                terms.add(value.lower())

        mapping[alpha_2.upper()] = terms

    return mapping


COUNTRY_SEARCH_TERMS_MAP = build_country_search_terms_map()


def country_search_terms(country_value):
    code = str(country_value or "").strip().upper()
    return COUNTRY_SEARCH_TERMS_MAP.get(code, {code.lower()} if code else set())


@st.cache_data(show_spinner=False)
def build_airport_rich_labels():
    labels = {}

    for code, info in AIRPORTS.items():
        flag = country_flag_emoji(info.get("country", ""))
        country_name = country_display_name(info.get("country", ""))
        prefix = f"{flag} " if flag else ""

        labels[code] = (
            f"{prefix}{code} \u2014 {info.get('city', code)}, "
            f"{country_name} ({info.get('name', code)})"
        )

    return labels


AIRPORT_RICH_LABELS = build_airport_rich_labels()


def airport_rich_label(code):
    return AIRPORT_RICH_LABELS.get(code, code)


@st.cache_data(show_spinner=False)
def build_airport_search_index():
    """
    Precomputes, per airport code, the lowercase city, airport
    name, country code, country display name, and the full set
    of country search terms. Kept as raw fields (not one
    flattened string) so the ranking function can tell exactly
    which field matched.
    """
    index = {}

    for code, info in AIRPORTS.items():
        country_value = info.get("country", "")

        index[code] = {
            "code": code.lower(),
            "city": str(info.get("city", "")).lower().strip(),
            "name": str(info.get("name", "")).lower().strip(),
            "country_code": str(country_value).lower().strip(),
            "country_display": country_display_name(country_value).lower().strip(),
            "country_terms": country_search_terms(country_value),
        }

    return index


AIRPORT_SEARCH_INDEX = build_airport_search_index()


def search_airports(query, limit=50):
    """
    Deterministic, fully controlled matching - no reliance on any
    built-in fuzzy/browser filter. Priority (lower = shown first):
      0 - exact IATA code match. If ANY code matches exactly, that
          single result is returned alone (e.g. "BKK" -> only BKK),
          since an exact code is unambiguous and should never be
          diluted with loosely related airports.
      1 - query exactly matches the country name (returns EVERY
          airport in that country, e.g. "Thailand" or "Japan").
      2 - country name starts with the query.
      3 - query exactly matches the city (e.g. "Tokyo" -> every
          airport serving Tokyo).
      4 - city starts with the query.
      5 - query appears anywhere in the country's search terms.
      6 - query appears anywhere in the city name.
      7 - query appears anywhere in the airport name.
      8 - every query word appears somewhere across all fields.
    """
    query = query.strip().lower()

    if not query:
        return []

    tokens = query.split()
    results = []
    exact_code_matches = []

    for code, idx in AIRPORT_SEARCH_INDEX.items():
        if idx["code"] == query:
            exact_code_matches.append(code)
            continue

        priority = None

        if query == idx["country_display"] or query in idx["country_terms"]:
            priority = 1
        elif idx["country_display"].startswith(query):
            priority = 2
        elif idx["city"] == query:
            priority = 3
        elif idx["city"].startswith(query):
            priority = 4
        elif any(query in term for term in idx["country_terms"]):
            priority = 5
        elif query in idx["city"]:
            priority = 6
        elif query in idx["name"]:
            priority = 7
        else:
            combined = " ".join(
                [
                    idx["code"],
                    idx["city"],
                    idx["name"],
                    idx["country_code"],
                    idx["country_display"],
                    " ".join(idx["country_terms"]),
                ]
            )

            if all(tok in combined for tok in tokens):
                priority = 8

        if priority is not None:
            results.append((priority, idx["city"], code))

    # An exact IATA code match is unambiguous - return ONLY that,
    # exactly matching the requirement that typing "BKK" shows
    # nothing but Bangkok's BKK.
    if exact_code_matches:
        return sorted(exact_code_matches)[:limit]

    results.sort(key=lambda r: (r[0], r[1]))

    return [code for _, _, code in results[:limit]]


def airport_search_function(searchterm):
    """
    Adapter for st_searchbox: called live on every keystroke with
    the current search text, must return a list of (label, value)
    tuples. Returns nothing until the user has typed something.
    """
    if not searchterm or not searchterm.strip():
        return []

    codes = search_airports(searchterm, limit=50)

    return [(airport_rich_label(code), code) for code in codes]


def smart_airport_select(label, default_code, key):
    """
    Renders one live search box (streamlit-searchbox): typing
    calls airport_search_function on every keystroke - no Enter,
    no blur, no button click needed - and the suggestion list
    updates immediately below the box. default_code may be None,
    in which case nothing is preselected and the field starts
    empty until the user searches and picks an airport.
    """
    selected_code = st_searchbox(
        airport_search_function,
        key=f"{key}_searchbox",
        label=label,
        placeholder="Search a country, city, airport name, or IATA code",
        default=default_code,
        default_options=(
            [airport_rich_label(default_code)] if default_code else []
        ),
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

    return round((1 - t if invert else t) * 100, 1)


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

    resp = requests.get(
        SERPAPI_URL,
        params=p,
        timeout=60,
    )

    try:
        data = resp.json()
    except Exception as exc:
        raise RuntimeError(
            f"SerpAPI returned a non-JSON response. HTTP {resp.status_code}"
        ) from exc

    if resp.status_code != 200 or "error" in data:
        raise RuntimeError(data.get("error", f"HTTP {resp.status_code}"))

    return data


# ============================================================
# OVERPASS NEARBY POINTS OF INTEREST
#
# 1. English names: request name:en (falling back to int_name /
#    alt_name:en), preferring it over the local-language "name"
#    tag for the display name.
# 2. Quality ranking: flag a POI notable when it carries a
#    wikipedia or wikidata tag (proxy for well documented,
#    recognizable places). Sort notable-first, then by distance
#    to the city center.
# ============================================================

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
    [out:json][timeout:25];
    (
      nwr["tourism"~"attraction|museum|viewpoint|gallery|zoo|artwork"](around:{radius_m},{lat},{lon});
      nwr["amenity"~"restaurant|cafe|bar"](around:{radius_m},{lat},{lon});
    );
    out center tags 200;
    """

    headers = {
        "User-Agent": "flight-hotel-recommender-streamlit/1.0"
    }

    for url in OVERPASS_URLS:
        try:
            resp = requests.post(
                url,
                data={"data": query},
                headers=headers,
                timeout=30,
            )

            if resp.status_code in (429, 500, 502, 503, 504):
                continue

            resp.raise_for_status()
            data = resp.json()

            rows = []

            for el in data.get("elements", []):
                tags = el.get("tags", {}) or {}

                local_name = tags.get("name")
                english_name = (
                    tags.get("name:en")
                    or tags.get("int_name")
                    or tags.get("alt_name:en")
                )

                display_name = english_name or local_name

                if not display_name:
                    continue

                center = el.get("center", {}) or {}
                poi_lat = el.get("lat", center.get("lat"))
                poi_lon = el.get("lon", center.get("lon"))

                if poi_lat is None or poi_lon is None:
                    continue

                category = (
                    "restaurant"
                    if tags.get("amenity") in ("restaurant", "cafe", "bar")
                    else "attraction"
                )

                is_notable = bool(
                    tags.get("wikipedia") or tags.get("wikidata")
                )

                dist_from_center = haversine_km(lat, lon, poi_lat, poi_lon)

                rows.append(
                    {
                        "name": display_name,
                        "local_name": local_name,
                        "category": category,
                        "lat": poi_lat,
                        "lon": poi_lon,
                        "cuisine": tags.get("cuisine"),
                        "tourism_type": tags.get("tourism"),
                        "notable": is_notable,
                        "dist_from_center_km": round(dist_from_center, 2),
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

            df = df.sort_values(
                by=["notable", "dist_from_center_km"],
                ascending=[False, True],
            ).reset_index(drop=True)

            return df[POI_COLUMNS].reset_index(drop=True)

        except Exception:
            continue

    return pd.DataFrame(columns=POI_COLUMNS)


def poi_count_near(df_pois, lat, lon, km=2.0):
    if df_pois.empty or "lat" not in df_pois.columns:
        return 0

    valid = df_pois.dropna(subset=["lat", "lon"])

    if valid.empty:
        return 0

    d = valid.apply(
        lambda r: haversine_km(lat, lon, r["lat"], r["lon"]),
        axis=1,
    )

    return int((d <= km).sum())


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

    for f in all_flights:
        legs = f.get("flights", [])

        if not legs:
            continue

        first_leg = legs[0]
        last_leg = legs[-1]

        flight_rows.append(
            {
                "price": f.get("price"),
                "duration_min": f.get("total_duration"),
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
        raise RuntimeError("Flights were returned but none had price data.")

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

    for h in hotel_json.get("properties", []):
        gps = h.get("gps_coordinates") or {}

        if "latitude" not in gps or "longitude" not in gps:
            continue

        price = (h.get("rate_per_night") or {}).get("extracted_lowest")

        hotel_rows.append(
            {
                "name": h.get("name"),
                "price": price,
                "rating": h.get("overall_rating"),
                "reviews": h.get("reviews"),
                "lat": gps["latitude"],
                "lon": gps["longitude"],
                "link": h.get("link"),
            }
        )

    df_hotels = pd.DataFrame(hotel_rows)

    if df_hotels.empty:
        raise RuntimeError("No hotels with location data were returned for this city.")

    df_hotels = df_hotels.dropna(subset=["price", "rating", "lat", "lon"]).head(12)

    if df_hotels.empty:
        raise RuntimeError("No hotels with complete price/rating/location data were returned.")

    city_center_lat = df_hotels["lat"].mean()
    city_center_lon = df_hotels["lon"].mean()

    df_pois = overpass_nearby(
        city_center_lat,
        city_center_lon,
        radius_m,
    )

    df_hotels["airport_dist_km"] = df_hotels.apply(
        lambda r: round(
            haversine_km(
                r["lat"],
                r["lon"],
                arr["lat"],
                arr["lon"],
            ),
            1,
        ),
        axis=1,
    )

    df_hotels["poi_count"] = df_hotels.apply(
        lambda r: poi_count_near(
            df_pois,
            r["lat"],
            r["lon"],
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
        lambda v: norm_score(
            v,
            df["price"].min(),
            df["price"].max(),
            invert=True,
        )
    )

    df["rating_score"] = (df["rating"] / 5 * 100).round(1)

    df["airport_score"] = df["airport_dist_km"].apply(
        lambda v: norm_score(
            v,
            df["airport_dist_km"].min(),
            df["airport_dist_km"].max(),
            invert=True,
        )
    )

    df["poi_score"] = df["poi_count"].apply(
        lambda v: norm_score(
            v,
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
#
# Palette sourced directly from the user-provided "GENERAL
# SPECTRUM" swatch strip (cream -> muted sage/teal -> slate ->
# navy -> near-black), extracted by sampling the actual pixel
# colors of the reference image. Two-tone layout, as requested:
#   - Sidebar: darkest end of the spectrum (near-black backdrop,
#     dark navy accent) - reads as "dark blue".
#   - Main / results page: lightest end of the spectrum (cream
#     background, muted slate-teal accent) - a light, modern
#     look built from the same family of colors as the sidebar.
# ============================================================

# --- Main content / results page: light, from the spectrum's
#     cream/sage end ---
BG = "#F5F2E9"
PANEL = "#FFFFFF"
PANEL_ALT = "#ECE9DD"
BORDER = "#D7D6C6"
AMBER = "#46595F"
AMBER_SOFT = "#E3E9E7"
CYAN = "#5C6B57"
ROSE = "#4A4C42"
TEXT = "#20241E"
TEXT_MUTED = "#6C7266"
TEXT_DIM = "#98A091"

# --- Sidebar: dark, from the spectrum's navy/near-black end ---
SB_BG = "#0A0F0D"
SB_PANEL = "#122120"
SB_PANEL_ALT = "#182B29"
SB_BORDER = "#2A403D"
SB_ACCENT = "#3E7C93"
SB_ACCENT_SOFT = "#123543"
SB_CYAN = "#6FA89A"
SB_TEXT = "#EFF6F1"
SB_TEXT_MUTED = "#93A69E"
SB_TEXT_DIM = "#5D7268"


# ============================================================
# CSS
# ============================================================

st.markdown(
    f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Sora:wght@500;600;700;800&family=Plus+Jakarta+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap');

html, body, [class*="css"] {{
font-family: 'Plus Jakarta Sans', sans-serif;
}}

/* Main content / results page - light spectrum tones */
.stApp {{
background:
radial-gradient(circle at 15% -10%, {AMBER_SOFT} 0%, transparent 45%),
radial-gradient(circle at 100% 0%, {CYAN}14 0%, transparent 40%),
{BG};
color: {TEXT};
}}

h1, h2, h3, h4 {{
font-family: 'Sora', sans-serif !important;
font-weight: 600 !important;
color: {TEXT} !important;
}}

/* Sidebar - dark spectrum tones */
[data-testid="stSidebar"] {{
background:
radial-gradient(circle at 20% 0%, {SB_ACCENT_SOFT} 0%, transparent 55%),
{SB_BG};
border-right: 1px solid {SB_BORDER};
}}

[data-testid="stSidebar"] * {{
color: {SB_TEXT};
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

/* Eyebrow labels inside the sidebar (SEARCH, RANKING WEIGHTS,
   SHARE) use the sidebar's accent instead of the page's. */
[data-testid="stSidebar"] .fhp-eyebrow {{
color: {SB_ACCENT};
}}

.fhp-title {{
font-family: 'Sora', sans-serif;
font-weight: 800;
font-size: 34px;
margin: 0 0 6px;
color: {TEXT};
letter-spacing: -0.5px;
}}

.fhp-subtitle {{
color: {TEXT_MUTED};
font-size: 14px;
max-width: 720px;
line-height: 1.5;
margin-bottom: 8px;
}}

/* Buttons: Search / Reset live in the sidebar, so the base
   button style uses the sidebar's dark palette. */
.stButton > button {{
border-radius: 10px;
border: 1px solid {SB_BORDER};
background: {SB_PANEL_ALT};
color: {SB_TEXT};
font-family: 'Plus Jakarta Sans', sans-serif;
font-weight: 500;
transition: border-color 0.15s ease, transform 0.05s ease;
}}

.stButton > button:hover {{
border-color: {SB_ACCENT};
color: {SB_TEXT};
}}

.stButton > button:active {{
transform: scale(0.98);
}}

.stButton > button[kind="primary"] {{
background: linear-gradient(135deg, {SB_ACCENT} 0%, #245266 100%);
color: #FFFFFF;
border: none;
font-weight: 700;
box-shadow: 0 4px 16px {SB_ACCENT}55;
}}

.stButton > button[kind="primary"]:hover {{
filter: brightness(1.12);
color: #FFFFFF;
}}

/* Download button lives in the main content area (EXPORT
   section), so it follows the light page palette. */
.stDownloadButton > button {{
border-radius: 10px;
border: 1px solid {BORDER};
background: {PANEL_ALT};
color: {TEXT};
font-weight: 600;
}}

.stDownloadButton > button:hover {{
border-color: {AMBER};
color: {AMBER};
}}

/* Text inputs, date inputs, and selects all live inside the
   sidebar (airport search, dates, currency). */
[data-testid="stTextInput"] input,
[data-testid="stDateInput"] input,
[data-baseweb="select"] > div {{
background: {SB_PANEL_ALT} !important;
border: 1px solid {SB_BORDER} !important;
color: {SB_TEXT} !important;
border-radius: 8px !important;
font-family: 'JetBrains Mono', monospace !important;
font-size: 13px !important;
}}

/* Sliders (ranking weights, POI radius) also live in the
   sidebar. */
[data-testid="stSlider"] [role="slider"] {{
background-color: {SB_ACCENT} !important;
border-color: {SB_ACCENT} !important;
}}

[data-testid="stSlider"] div[data-baseweb="slider"] > div > div {{
background: {SB_ACCENT} !important;
}}

[data-testid="stSidebar"] [data-testid="stCaptionContainer"] {{
color: {SB_TEXT_MUTED} !important;
}}

/* Main content: dataframe, expander, tabs - light page palette */
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

/* Share expander lives in the dark sidebar. The rule above sets
   a light/white background (matching the light main-page style),
   but the broad "[data-testid=stSidebar] *" text-color rule would
   otherwise also paint its header text near-white, making it
   unreadable on that white background. Force the header (and any
   paragraph text inside it) to a dark color specifically here. */
[data-testid="stSidebar"] [data-testid="stExpander"] {{
border: 1px solid {SB_BORDER};
}}

[data-testid="stSidebar"] [data-testid="stExpander"] summary,
[data-testid="stSidebar"] [data-testid="stExpander"] summary p,
[data-testid="stSidebar"] [data-testid="stExpander"] summary span,
[data-testid="stSidebar"] [data-testid="stExpander"] summary svg {{
color: #1B1F30 !important;
fill: #1B1F30 !important;
font-weight: 600;
}}

[data-baseweb="tab-list"] {{
gap: 4px;
}}

[data-baseweb="tab"] {{
background: {PANEL_ALT};
border: 1px solid {BORDER};
border-radius: 20px !important;
padding: 4px 16px !important;
color: {TEXT_MUTED} !important;
}}

[aria-selected="true"][data-baseweb="tab"] {{
background: {AMBER_SOFT} !important;
color: {AMBER} !important;
border-color: {AMBER} !important;
}}

.fhp-card {{
background: {PANEL};
border: 1px solid {BORDER};
border-radius: 14px;
padding: 16px;
margin-bottom: 10px;
box-shadow: 0 2px 10px rgba(32,36,30,0.06);
transition: border-color 0.15s ease, box-shadow 0.15s ease;
}}

.fhp-card:hover {{
border-color: {AMBER}88;
box-shadow: 0 6px 18px rgba(70,89,95,0.14);
}}

.fhp-card-top1 {{
border-color: {AMBER};
box-shadow: 0 6px 20px {AMBER}22;
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

.fhp-badge-notable {{
display: inline-flex;
align-items: center;
gap: 4px;
font-size: 10px;
color: {AMBER};
font-family: 'JetBrains Mono', monospace;
border: 1px solid {AMBER};
border-radius: 6px;
padding: 1px 6px;
margin-left: 6px;
background: {AMBER_SOFT};
}}

.fhp-search-divider {{
height: 1px;
background: linear-gradient(90deg, transparent, {SB_BORDER}, transparent);
margin: 14px 0;
}}

/* Share QR section */
.fhp-share-box {{
background: {SB_PANEL};
border: 1px solid {SB_BORDER};
border-radius: 12px;
padding: 14px;
text-align: center;
}}

.fhp-share-caption {{
color: {SB_TEXT_MUTED};
font-size: 11px;
margin-top: 8px;
line-height: 1.5;
}}
</style>
""",
    unsafe_allow_html=True,
)


# ============================================================
# HEADER
# ============================================================

render_html('<div class="fhp-eyebrow">FLIGHT PATH RECOMMENDER</div>')
render_html('<div class="fhp-title">Flight route + hotel recommender</div>')
render_html(
    '<div class="fhp-subtitle">'
    'Real flight and hotel data via SerpAPI Google Flights and Google Hotels. '
    'Nearby attractions and restaurants via OpenStreetMap Overpass, shown with '
    'English names where available and ranked by notability and proximity. '
    'Every IATA airport is searchable by country, city, airport name, or IATA code '
    '- results filter live as you type, no need to press Enter.'
    '</div>'
)


# ============================================================
# SESSION STATE
# ============================================================

if "raw" not in st.session_state:
    st.session_state.raw = None

if "search_error" not in st.session_state:
    st.session_state.search_error = None


# ============================================================
# SHARE QR CODE
# Generates a QR code for the deployed app's public URL. Tries
# a local render via the qrcode package first (crisper, no
# network call at render time); if that package is not
# installed, falls back to the free api.qrserver.com image API,
# which needs no extra dependency at all.
# ============================================================

def render_share_qr_section():
    render_html('<div class="fhp-eyebrow" style="margin-top:4px;">SHARE</div>')

    with st.expander("Share this app", expanded=False):
        qr_image = None

        try:
            import qrcode

            qr = qrcode.QRCode(border=1, box_size=8)
            qr.add_data(APP_SHARE_URL)
            qr.make(fit=True)
            img = qr.make_image(fill_color=SB_TEXT, back_color=SB_PANEL)

            buf = BytesIO()
            img.save(buf, format="PNG")
            qr_image = buf.getvalue()
        except Exception:
            qr_image = None

        render_html('<div class="fhp-share-box">')

        if qr_image:
            st.image(qr_image, width=180)
        else:
            qr_api_url = (
                "https://api.qrserver.com/v1/create-qr-code/"
                f"?size=220x220&data={requests.utils.quote(APP_SHARE_URL, safe='')}"
            )
            st.image(qr_api_url, width=180)

        render_html(
            '<div class="fhp-share-caption">Scan to open this app on another device</div>'
        )
        render_html("</div>")

        st.code(APP_SHARE_URL, language=None)


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:
    render_html('<div class="fhp-eyebrow" style="margin-top:-8px;">SEARCH</div>')

    st.success("SerpAPI key loaded automatically from Streamlit Secrets")

    st.caption(
        "Search by country (e.g. Japan, Thailand), city (e.g. Tokyo, Bangkok), "
        "airport name, or exact IATA code (e.g. BKK). Results update as you type."
    )

    # No pre-selected default airport for either field - the user
    # must actively search and pick both before running a search.
    dep_iata = smart_airport_select(
        label="Departure airport",
        default_code=None,
        key="departure",
    )

    arr_iata = smart_airport_select(
        label="Arrival airport",
        default_code=None,
        key="arrival",
    )

    render_html('<div class="fhp-search-divider"></div>')

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

    render_html('<div class="fhp-eyebrow">RANKING WEIGHTS</div>')
    st.caption("These apply instantly to whatever result is on screen. No need to search again.")

    w_price = st.slider("Price", 0, 100, 25, 5)
    w_rating = st.slider("Guest rating", 0, 100, 25, 5)
    w_airport = st.slider("Near airport", 0, 100, 25, 5)
    w_poi = st.slider("Near attractions and restaurants", 0, 100, 25, 5)

    radius_m = st.slider(
        "POI search radius (m)",
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

    render_html('<div class="fhp-search-divider"></div>')

    render_share_qr_section()


# ============================================================
# BUTTON ACTIONS
# ============================================================

if reset_clicked:
    st.session_state.raw = None
    st.session_state.search_error = None
    st.rerun()


if search_clicked:
    if not dep_iata or not arr_iata:
        st.session_state.search_error = (
            "Please search and select both a departure and an arrival airport first."
        )

    elif dep_iata == arr_iata:
        st.session_state.search_error = "Departure and arrival airports must be different."

    elif return_date <= outbound_date:
        st.session_state.search_error = "Return date must be after outbound date."

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
            "Search airport by country, city, airport name, or IATA code - the list "
            "filters as you type. Example: type Japan to see airports in Japan, then "
            "select one and click Search."
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

duration_str = (
    f"{int(cheapest['duration_min'] // 60)}h {int(cheapest['duration_min'] % 60)}m"
    if pd.notna(cheapest["duration_min"])
    else "n/a"
)


# ============================================================
# ROUTE
# ============================================================

render_html('<div class="fhp-eyebrow" style="margin-top:28px;">ROUTE</div>')
render_html(f'<div class="fhp-title" style="font-size:22px;">{dep_iata} to {arr_iata}</div>')

stat_cols = st.columns(4)

stats = [
    ("Cheapest price", f"{cheapest['price']} {currency}"),
    ("Duration", duration_str),
    ("Arrival, local", cheapest["arrival_time"] or "n/a"),
    ("Arrival window", "Night" if is_night else "Day"),
]

for col, (label, value) in zip(stat_cols, stats):
    accent = AMBER if label == "Arrival window" else TEXT

    with col:
        render_html(
            f'<div style="background:{PANEL};border:1px solid {BORDER};border-radius:10px;padding:12px 16px;box-shadow:0 2px 8px rgba(32,36,30,0.05);">'
            f'<div style="font-size:11px;color:{TEXT_MUTED};text-transform:uppercase;letter-spacing:1px;margin-bottom:4px;">{label}</div>'
            f'<div style="font-family:\'JetBrains Mono\',monospace;font-size:18px;color:{accent};">{value}</div>'
            f'</div>'
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

render_html('<div class="fhp-eyebrow" style="margin-top:32px;">HOTELS</div>')
render_html(f'<div class="fhp-title" style="font-size:22px;">Recommended in {arr["city"]}</div>')
render_html(
    '<div class="fhp-subtitle">'
    'Score blends price, guest rating, distance to the arrival airport, '
    'and density of nearby attractions/restaurants, weighted by your sidebar sliders.'
    '</div>'
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

for _, h in df_hotels.iterrows():
    is_top = h["rank"] == 1

    rank_bg = AMBER_SOFT if is_top else PANEL_ALT
    rank_color = AMBER if is_top else TEXT_MUTED

    bars = [
        ("Price", h["price_score"]),
        ("Rating", h["rating_score"]),
        ("Airport", h["airport_score"]),
        ("Nearby", h["poi_score"]),
    ]

    bar_parts = []
    for label, b in bars:
        bar_parts.append(
            f'<div style="flex:1;min-width:70px;">'
            f'<div class="fhp-bar-track"><div class="fhp-bar-fill" style="width:{b}%;"></div></div>'
            f'<div style="font-size:10px;color:{TEXT_DIM};margin-top:3px;">{label}</div>'
            f'</div>'
        )
    bars_html = "".join(bar_parts)

    rating_value = h["rating"] if pd.notna(h["rating"]) else 0
    full_stars = max(0, min(5, round(rating_value)))
    stars = "\u2605" * full_stars + "\u2606" * (5 - full_stars)

    reviews_value = int(h["reviews"]) if pd.notna(h["reviews"]) else 0

    card_html = (
        f'<div class="fhp-card{" fhp-card-top1" if is_top else ""}">'
        f'<div style="display:flex;gap:14px;flex-wrap:wrap;">'
        f'<div class="fhp-rank" style="background:{rank_bg};color:{rank_color};">{h["rank"]}</div>'
        f'<div style="flex:1;min-width:220px;">'
        f'<div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px;">'
        f'<div style="font-family:\'Sora\',sans-serif;font-weight:600;font-size:15px;">{h["name"]}</div>'
        f'<div style="font-family:\'JetBrains Mono\',monospace;font-size:15px;color:{AMBER};">{h["price"]} {currency}'
        f'<span style="color:{TEXT_MUTED};font-size:11px;"> /night</span></div>'
        f'</div>'
        f'<div style="display:flex;gap:16px;flex-wrap:wrap;margin:6px 0 10px;">'
        f'<span class="fhp-tag" style="color:{AMBER};">{stars}'
        f'<span style="color:{TEXT_MUTED};">&nbsp;{h["rating"]} ({reviews_value})</span></span>'
        f'<span class="fhp-tag">Airport {h["airport_dist_km"]} km</span>'
        f'<span class="fhp-tag">Nearby {h["poi_count"]} spots</span>'
        f'</div>'
        f'<div style="display:flex;gap:10px;flex-wrap:wrap;">{bars_html}</div>'
        f'</div>'
        f'<div class="fhp-score" style="align-self:center;color:{AMBER if is_top else TEXT};min-width:46px;text-align:right;">{h["composite"]:.0f}</div>'
        f'</div>'
        f'</div>'
    )

    render_html(card_html)


# ============================================================
# MAP
# ============================================================

render_html('<div class="fhp-eyebrow" style="margin-top:28px;">MAP</div>')
render_html('<div class="fhp-title" style="font-size:22px;">Route and destination</div>')

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

for _, h in df_hotels.iterrows():
    color = "orange" if h["rank"] == 1 else "cadetblue"

    folium.Marker(
        [h["lat"], h["lon"]],
        tooltip=f"#{h['rank']} {h['name']} | {h['price']} {currency}, score {h['composite']:.0f}",
        icon=folium.Icon(color=color, icon="bed", prefix="fa"),
    ).add_to(m)

if not df_pois.empty:
    for _, poi in df_pois.dropna(subset=["lat", "lon"]).head(60).iterrows():
        color = CYAN if poi["category"] == "attraction" else ROSE
        star_prefix = "* " if poi.get("notable") else ""

        folium.CircleMarker(
            [poi["lat"], poi["lon"]],
            radius=4,
            color=color,
            fill=True,
            fill_opacity=0.8,
            tooltip=f"{star_prefix}{poi['name']} ({poi['category']})",
        ).add_to(m)

bounds = (
    [[dep["lat"], dep["lon"]], [arr["lat"], arr["lon"]]]
    + df_hotels[["lat", "lon"]].values.tolist()
)

m.fit_bounds(bounds)

render_html(f'<div style="border:1px solid {BORDER};border-radius:12px;overflow:hidden;">')

components.html(
    m._repr_html_(),
    height=560,
    scrolling=False,
)

render_html("</div>")


# ============================================================
# NEARBY
# ============================================================

render_html('<div class="fhp-eyebrow" style="margin-top:32px;">NEARBY</div>')
render_html(f'<div class="fhp-title" style="font-size:22px;">In {arr["city"]}</div>')
render_html(
    '<div class="fhp-subtitle">'
    'English names are shown where OpenStreetMap has one tagged; otherwise the local '
    f'name is shown. Places marked <span style="color:{AMBER};font-weight:600;">Notable</span> '
    'have a Wikipedia or Wikidata reference, used as a proxy for well-known, worth-visiting '
    'spots. The list leads with notable, centrally located places first.'
    '</div>'
)

if df_pois.empty:
    st.caption(
        "No attractions or restaurants found from Overpass. "
        "This can happen if the public Overpass server is busy, blocked, "
        "or there are no OSM results in the selected radius."
    )

else:
    for col in POI_COLUMNS:
        if col not in df_pois.columns:
            df_pois[col] = None

    tab_all, tab_attr, tab_food = st.tabs(
        ["All", "Attractions", "Restaurants"]
    )

    for tab, cat in [
        (tab_all, None),
        (tab_attr, "attraction"),
        (tab_food, "restaurant"),
    ]:
        with tab:
            shown = df_pois if cat is None else df_pois[df_pois["category"] == cat]

            shown = shown.head(30)

            if shown.empty:
                st.caption("No records found for this category.")
                continue

            cols = st.columns(3)

            for i, (_, poi) in enumerate(shown.iterrows()):
                is_food = poi["category"] == "restaurant"
                icon = "Food" if is_food else "Place"
                color = ROSE if is_food else CYAN

                if is_food and pd.notna(poi["cuisine"]):
                    meta = poi["cuisine"]
                elif pd.notna(poi["tourism_type"]):
                    meta = poi["tourism_type"]
                else:
                    meta = poi["category"]

                show_local = (
                    pd.notna(poi.get("local_name"))
                    and poi.get("local_name")
                    and poi.get("local_name") != poi.get("name")
                )

                local_name_html = (
                    f'<div style="font-size:10px;color:{TEXT_DIM};margin-top:2px;">{poi["local_name"]}</div>'
                    if show_local
                    else ""
                )

                notable_badge_html = (
                    '<span class="fhp-badge-notable">Notable</span>'
                    if poi.get("notable")
                    else ""
                )

                poi_card_html = (
                    f'<div style="background:{PANEL};border:1px solid {BORDER};border-radius:10px;padding:12px 14px;box-shadow:0 2px 8px rgba(32,36,30,0.05);">'
                    f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">'
                    f'<span style="color:{color};font-size:11px;font-family:\'JetBrains Mono\',monospace;">{icon}</span>'
                    f'<span style="font-family:\'Sora\',sans-serif;font-weight:600;font-size:13px;">{poi["name"]}</span>'
                    f'{notable_badge_html}'
                    f'</div>'
                    f'{local_name_html}'
                    f'<div class="fhp-tag" style="margin-top:4px;">{meta or ""}</div>'
                    f'</div>'
                )

                with cols[i % 3]:
                    render_html(poi_card_html)
                    render_html("<div style='height:8px;'></div>")


# ============================================================
# EXPORT
# ============================================================

render_html('<div class="fhp-eyebrow" style="margin-top:32px;">EXPORT</div>')

csv_data = hotel_display.to_csv(index=False).encode("utf-8")

st.download_button(
    label="Download hotel ranking as CSV",
    data=csv_data,
    file_name=f"hotel_ranking_{dep_iata}_to_{arr_iata}.csv",
    mime="text/csv",
    use_container_width=True,
)

render_html(
    f'<div style="color:{TEXT_DIM};font-size:12px;margin-top:12px;">'
    'Data: SerpAPI Google Flights, SerpAPI Google Hotels, '
    'OpenStreetMap contributors via Overpass API, '
    'and airport data via OurAirports public domain.'
    '</div>'
)
