# Flight + hotel recommender

Real flight and hotel data from SerpAPI (Google Flights / Google Hotels), real
nearby attractions and restaurants from OpenStreetMap's Overpass API (free, no
key needed), plotted on a real interactive map.

## Deploy it as a live web app (GitHub + Streamlit Community Cloud, free)

1. Push this folder to a new GitHub repo. **Do not commit a real API key** —
   `.gitignore` already excludes `.streamlit/secrets.toml`; only
   `secrets.toml.example` (a template with no real key) should be tracked.
2. Go to [share.streamlit.io](https://share.streamlit.io), sign in with
   GitHub, and click "New app". Point it at this repo, branch `main`, and
   file path `app.py`.
3. Before (or after) deploying, open the app's **Settings -> Secrets** in the
   Streamlit Cloud dashboard and add:
   ```toml
   SERPAPI_KEY = "your-real-key"
   ```
   This is Streamlit Cloud's own secret store — it's never written into your
   git history. The app reads it as `st.secrets["SERPAPI_KEY"]` and
   pre-fills the sidebar field with it, so visitors don't have to paste a
   key to use it. They can still paste their own in the sidebar if they'd
   rather use their own SerpAPI quota.
4. Redeploy (Streamlit Cloud usually does this automatically on push). Your
   app gets a public `*.streamlit.app` URL.

If a key was ever pasted into a chat, a doc, or committed to git by mistake,
treat it as compromised and regenerate it at serpapi.com — don't reuse it.

## Running it locally instead

```bash
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # then edit in your real key, optional
streamlit run app.py
```

Then open the local URL Streamlit prints (usually http://localhost:8501).
Skip the `secrets.toml` copy if you'd rather just paste the key into the
sidebar each run.

## Using it


1. If the host set `SERPAPI_KEY` in secrets, the sidebar field is already
   filled in. Otherwise paste your own SerpAPI key there — it's kept only in
   your session, never hardcode it in the file, and never share it in chat
   or commit it to a repo. If a key has ever been pasted somewhere public,
   regenerate it at serpapi.com.
2. Pick a departure and arrival airport from the searchable dropdowns — every
   IATA-coded airport is available (~7,900, via the `airportsdata` package,
   sourced from OurAirports public-domain data). Type part of a code, city,
   or airport name to filter.
3. Pick your dates and currency.
4. Optionally drag the ranking-weight sliders (price / rating / airport
   proximity / nearby attractions & restaurants) before you search.
5. Click Search.

## What it does

- Calls `google_flights` for the route and pulls the cheapest option's real
  arrival time, used to flag a day vs. night arrival.
- Calls `google_hotels` for the arrival city and keeps listings with a price,
  rating, and GPS coordinates.
- Calls Overpass once around the average hotel location for nearby
  `tourism=attraction/museum/viewpoint/gallery/zoo` and `amenity=restaurant`
  points.
- Scores each hotel as a weighted blend of price, rating, distance to the
  arrival airport, and count of nearby attractions/restaurants, then ranks
  them.
- Draws a real folium/OpenStreetMap map (embedded as static HTML — no
  bidirectional component, so nothing round-trips over the websocket): a
  great-circle flight path between the two airports, airport markers, hotel
  markers (top pick highlighted),
  and small markers for every nearby attraction/restaurant.

## Notes and known limits

- Airport coordinates are approximate — fine for ranking and mapping, not for
  navigation.
- Overpass is a shared free community service; it can be slow or rate-limit
  under heavy use. Results are cached for an hour per query to reduce load.
- There's no free, reliable local-events API. Ticketmaster's Discovery API
  has a free tier (needs a free signup) and would be the natural next
  addition — ask if you want that wired in.
- The great-circle path is a straight-line approximation of the route, not
  the airline's actual flight-planned track, and doesn't handle routes that
  cross the antimeridian (e.g. some transpacific pairs) cleanly.
