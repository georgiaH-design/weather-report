#!/usr/bin/env python3
"""
Captain Georgia — Daily East Coast Maritime Weather Report Generator
Fetches live NWS + NHC data and builds a self-contained HTML file.
Runs via GitHub Actions every morning at 0300 EDT (0700 UTC).
Uses official NWS JSON API (api.weather.gov) — no HTML scraping.
"""

import urllib.request
import urllib.error
import json
import re
import sys
import time
import os
from datetime import datetime, timezone, timedelta

EDT = timezone(timedelta(hours=-4))
now = datetime.now(EDT)

# Windy Point Forecast API key — set as WINDY_API_KEY GitHub secret
WINDY_API_KEY = os.environ.get("WINDY_API_KEY", "")
ISSUE_TIME = now.strftime("%A, %B %-d, %Y · %-I:%M %p EDT")
VALID_THRU = (now + timedelta(hours=48)).strftime("%A, %B %-d, %Y")

# ── helpers ──────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": "CaptainGeorgia-WeatherBot/1.0 (captaingeorgia.com)",
    "Accept": "application/geo+json, application/json"
}

def fetch_text(url):
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  WARN: fetch_text {url}: {e}", file=sys.stderr)
        return ""

def fetch_json(url, retries=3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            print(f"  WARN: fetch_json attempt {attempt+1} {url}: {e}", file=sys.stderr)
            if attempt < retries - 1:
                time.sleep(2)
    return None

def deg_to_compass(deg):
    if deg is None:
        return "N/A"
    dirs = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW","WSW","W","WNW","NW","NNW"]
    return dirs[round(float(deg) / 22.5) % 16]

def c_to_f(c):
    if c is None:
        return None
    return round(float(c) * 9 / 5 + 32)

def ms_to_kt(ms):
    if ms is None:
        return None
    return round(float(ms) * 1.94384)

def ms_to_mph(ms):
    if ms is None:
        return None
    return round(float(ms) * 2.23694)

def m_to_mi(m):
    if m is None:
        return None
    return round(float(m) / 1609.34, 1)

# ── NDBC buoy full data fetcher ──────────────────────────────────────────────

def fetch_buoy_full(buoy_id):
    """Fetch full obs from NDBC buoy: waves, wind, water temp, pressure, trend."""
    url = f"https://www.ndbc.noaa.gov/data/realtime2/{buoy_id}.txt"
    text = fetch_text(url)
    result = {
        "wvht": "N/A", "dpd": "N/A", "mwd": "N/A", "wspd": "N/A",
        "wtmp": "N/A", "pres": "N/A",
        "sea_label": "N/A", "sea_cls": "sea-calm",
        "trend": "→", "trend_label": "Steady", "trend_cls": "trend-steady"
    }
    if not text:
        return result
    lines = [l for l in text.strip().split('\n') if not l.startswith('#')]
    if not lines:
        return result

    def parse_row(row):
        p = row.split()
        if len(p) < 7:   # need at least through WSPD (col 6)
            return None
        def v(i): return None if i >= len(p) or p[i] == 'MM' else p[i]
        return {"wvht": v(8), "dpd": v(9), "mwd": v(11),
                "wspd": v(6), "wdir": v(5), "gust": v(7),
                "pres": v(12), "wtmp": v(14)}

    cur = parse_row(lines[0])
    if not cur:
        return result

    # For wave data, scan up to 12 most recent rows for the first non-MM reading
    wave_row = None
    for line in lines[:12]:
        row = parse_row(line)
        if row and row["wvht"] is not None:
            wave_row = row
            break
    if wave_row:
        cur["wvht"] = wave_row["wvht"]
        cur["dpd"]  = wave_row.get("dpd")
        cur["mwd"]  = wave_row.get("mwd")

    # Wave height + sea state
    if cur["wvht"]:
        try:
            wm = float(cur["wvht"])
            wf = round(wm * 3.28084, 1)
            result["wvht"] = f"{wf} ft"
            if wf < 2:   result["sea_label"], result["sea_cls"] = "CALM",       "sea-calm"
            elif wf < 4: result["sea_label"], result["sea_cls"] = "SLIGHT",     "sea-calm"
            elif wf < 8: result["sea_label"], result["sea_cls"] = "MODERATE",   "sea-moderate"
            elif wf < 13:result["sea_label"], result["sea_cls"] = "ROUGH",      "sea-rough"
            else:        result["sea_label"], result["sea_cls"] = "VERY ROUGH", "sea-very-rough"

            # Trend vs ~12 hrs ago
            if len(lines) > 12:
                past = parse_row(lines[12])
                if past and past["wvht"]:
                    diff = wm - float(past["wvht"])
                    if diff > 0.3:
                        result["trend"], result["trend_label"], result["trend_cls"] = "↑", "Building",  "trend-up"
                    elif diff < -0.3:
                        result["trend"], result["trend_label"], result["trend_cls"] = "↓", "Subsiding", "trend-down"
        except Exception:
            pass

    # Period
    if cur["dpd"]:
        try: result["dpd"] = f"{round(float(cur['dpd']))} sec"
        except: pass

    # Wave direction
    if cur["mwd"]:
        try: result["mwd"] = f"{deg_to_compass(cur['mwd'])} {round(float(cur['mwd']))}°"
        except: pass

    # Wind
    if cur["wspd"]:
        try:
            kt = ms_to_kt(cur["wspd"])
            compass = deg_to_compass(cur["wdir"])
            gust_str = f" G{ms_to_kt(cur['gust'])}kt" if cur.get("gust") else ""
            result["wspd"] = f"{compass} {kt}kt{gust_str}"
        except: pass

    # Water temp
    if cur["wtmp"]:
        try: result["wtmp"] = f"{c_to_f(cur['wtmp'])}°F"
        except: pass

    # Pressure
    if cur["pres"]:
        try: result["pres"] = f"{round(float(cur['pres']))} mb"
        except: pass

    return result

# ── Windy Point Forecast API — wave fallback ─────────────────────────────────

def fetch_windy_waves(lat, lon):
    """
    Fetch ECMWF wave forecast from Windy Point Forecast v2 API.
    Used as fallback when the NDBC buoy has no wave sensor (CHLV2, FWYF1, SMKF1).
    Returns a partial buoy-data dict with wvht, dpd, mwd, sea_label, sea_cls, trend* fields.
    """
    if not WINDY_API_KEY:
        print("  WARN: WINDY_API_KEY not set — skipping Windy fallback", file=sys.stderr)
        return None

    body = json.dumps({
        "lat": lat,
        "lon": lon,
        "model": "ecmwf",
        "parameters": ["waves_sig_ht", "waves_mean_per", "waves_dir"],
        "levels": ["surface"],
        "key": WINDY_API_KEY
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            "https://api.windy.com/api/point-forecast/v2",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"  WARN: Windy API error at ({lat},{lon}): {e}", file=sys.stderr)
        return None

    ts_list = data.get("ts", [])
    if not ts_list:
        return None

    # Find the index of the timestamp closest to now (without going too far future)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    idx = 0
    for i, ts in enumerate(ts_list):
        if ts >= now_ms:
            idx = i
            break

    result = {"source": "windy"}

    # Wave height → feet, sea state
    wh_key = "waves_sig_ht-surface"
    wh = data.get(wh_key) or []
    if wh and idx < len(wh) and wh[idx] is not None:
        try:
            wm = float(wh[idx])
            wf = round(wm * 3.28084, 1)
            result["wvht"] = f"{wf} ft"
            if wf < 2:    result["sea_label"], result["sea_cls"] = "CALM",       "sea-calm"
            elif wf < 4:  result["sea_label"], result["sea_cls"] = "SLIGHT",     "sea-calm"
            elif wf < 8:  result["sea_label"], result["sea_cls"] = "MODERATE",   "sea-moderate"
            elif wf < 13: result["sea_label"], result["sea_cls"] = "ROUGH",      "sea-rough"
            else:         result["sea_label"], result["sea_cls"] = "VERY ROUGH", "sea-very-rough"

            # 24-hr trend: compare idx vs ~4 steps back (Windy uses 3-hr intervals)
            old_idx = max(0, idx - 8)  # ~24 hrs ago
            if old_idx != idx and wh[old_idx] is not None:
                diff = wm - float(wh[old_idx])
                if diff > 0.3:
                    result["trend"], result["trend_label"], result["trend_cls"] = "↑", "Building",  "trend-up"
                elif diff < -0.3:
                    result["trend"], result["trend_label"], result["trend_cls"] = "↓", "Subsiding", "trend-down"
                else:
                    result["trend"], result["trend_label"], result["trend_cls"] = "→", "Steady",    "trend-steady"
        except Exception:
            pass

    # Wave period
    mp = data.get("waves_mean_per-surface") or []
    if mp and idx < len(mp) and mp[idx] is not None:
        try:
            result["dpd"] = f"{round(float(mp[idx]))} sec"
        except Exception:
            pass

    # Wave direction
    wd = data.get("waves_dir-surface") or []
    if wd and idx < len(wd) and wd[idx] is not None:
        try:
            deg = float(wd[idx])
            result["mwd"] = f"{deg_to_compass(deg)} {round(deg)}°"
        except Exception:
            pass

    return result if result.get("wvht") else None

# ── Open-Meteo Marine API — free wave fallback (no key required) ─────────────

def fetch_openmeteo_marine(lat, lon):
    """
    Fetch wave forecast from Open-Meteo Marine API.
    Free, no API key, global coverage. Used when NDBC buoy has no wave sensor
    and no Windy key is available (or as primary free fallback).
    """
    url = (f"https://marine-api.open-meteo.com/v1/marine"
           f"?latitude={lat}&longitude={lon}"
           f"&hourly=wave_height,wave_period,wave_direction"
           f"&timezone=UTC&forecast_days=1")
    data = fetch_json(url)
    if not data or "hourly" not in data:
        return None

    times  = data["hourly"].get("time", [])
    now_hr = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:00")
    idx    = 0
    for i, t in enumerate(times):
        if t <= now_hr:
            idx = i

    result = {"source": "openmeteo"}

    wh = data["hourly"].get("wave_height", [])
    wp = data["hourly"].get("wave_period", [])
    wd = data["hourly"].get("wave_direction", [])

    if wh and idx < len(wh) and wh[idx] is not None:
        try:
            wm = float(wh[idx])
            wf = round(wm * 3.28084, 1)
            result["wvht"] = f"{wf} ft"
            if wf < 2:    result["sea_label"], result["sea_cls"] = "CALM",       "sea-calm"
            elif wf < 4:  result["sea_label"], result["sea_cls"] = "SLIGHT",     "sea-calm"
            elif wf < 8:  result["sea_label"], result["sea_cls"] = "MODERATE",   "sea-moderate"
            elif wf < 13: result["sea_label"], result["sea_cls"] = "ROUGH",      "sea-rough"
            else:         result["sea_label"], result["sea_cls"] = "VERY ROUGH", "sea-very-rough"
            # 24-hr trend
            old_idx = max(0, idx - 24)
            if old_idx != idx and wh[old_idx] is not None:
                diff = wm - float(wh[old_idx])
                if diff > 0.3:
                    result["trend"], result["trend_label"], result["trend_cls"] = "↑", "Building",  "trend-up"
                elif diff < -0.3:
                    result["trend"], result["trend_label"], result["trend_cls"] = "↓", "Subsiding", "trend-down"
                else:
                    result["trend"], result["trend_label"], result["trend_cls"] = "→", "Steady",    "trend-steady"
        except Exception:
            pass

    if wp and idx < len(wp) and wp[idx] is not None:
        try:
            result["dpd"] = f"{round(float(wp[idx]))} sec"
        except Exception:
            pass

    if wd and idx < len(wd) and wd[idx] is not None:
        try:
            deg = float(wd[idx])
            result["mwd"] = f"{deg_to_compass(deg)} {round(deg)}°"
        except Exception:
            pass

    return result if result.get("wvht") else None

# ── NWS API data fetcher ──────────────────────────────────────────────────────

def fetch_nws_api(lat, lon, station_code):
    """Fetch current observations + 48-hr forecast via NWS JSON API."""
    data = {
        "temp": "N/A", "conditions": "N/A", "humidity": "N/A",
        "wind": "N/A", "visibility": "N/A", "heat_index": "", "wave_height": "N/A", "periods": []
    }

    # ── Current observations ──────────────────────────────────────────────────
    obs_url = f"https://api.weather.gov/stations/{station_code}/observations/latest"
    obs = fetch_json(obs_url)
    if obs and "properties" in obs:
        props = obs["properties"]

        temp_c = (props.get("temperature") or {}).get("value")
        temp_f = c_to_f(temp_c)
        if temp_f is not None:
            data["temp"] = f"{temp_f}°F"

        data["conditions"] = props.get("textDescription") or "N/A"

        hum = (props.get("relativeHumidity") or {}).get("value")
        if hum is not None:
            data["humidity"] = f"{round(float(hum))}%"

        wspd = (props.get("windSpeed") or {}).get("value")
        wdir = (props.get("windDirection") or {}).get("value")
        gust = (props.get("windGust") or {}).get("value")
        if wspd is not None:
            kt = ms_to_kt(wspd)
            mph = ms_to_mph(wspd)
            compass = deg_to_compass(wdir)
            gust_str = f" G{ms_to_kt(gust)}kt" if gust else ""
            data["wind"] = f"{compass} {kt}kt ({mph}mph){gust_str}"

        vis = (props.get("visibility") or {}).get("value")
        if vis is not None:
            data["visibility"] = f"{m_to_mi(vis)} mi"

        hi_c = (props.get("heatIndex") or {}).get("value")
        hi_f = c_to_f(hi_c)
        if hi_f is not None:
            data["heat_index"] = f"{hi_f}°F"

    # ── Forecast periods ──────────────────────────────────────────────────────
    points_url = f"https://api.weather.gov/points/{lat},{lon}"
    points = fetch_json(points_url)
    if points and "properties" in points:
        forecast_url = points["properties"].get("forecast")
        if forecast_url:
            time.sleep(0.3)   # be polite to the API
            forecast = fetch_json(forecast_url)
            if forecast and "properties" in forecast:
                for period in (forecast["properties"].get("periods") or [])[:10]:
                    name = period.get("name", "")
                    temp = f"{period.get('temperature', '')}°{period.get('temperatureUnit', 'F')}"
                    short = period.get("shortForecast", "")
                    detail = period.get("detailedForecast", "")
                    wind_spd = period.get("windSpeed", "")
                    wind_dir = period.get("windDirection", "")
                    wind_str = f" · Wind {wind_dir} {wind_spd}" if wind_spd else ""
                    desc = f"{short}{wind_str}"
                    if detail:
                        desc = detail[:200]
                    data["periods"].append({"name": name, "temp": temp, "desc": desc})

    return data

# ── Coastal Waters + NHC ──────────────────────────────────────────────────────

def fetch_cwf(site):
    """Fetch Coastal Waters Forecast text product."""
    url = (f"https://forecast.weather.gov/product.php"
           f"?site={site}&issuedby={site}&product=CWF&format=txt&version=1")
    html = fetch_text(url)
    m = re.search(r'<pre[^>]*>(.*?)</pre>', html, re.DOTALL)
    return m.group(1).strip() if m else ""

def fetch_nhc_two():
    """Fetch NHC Atlantic Tropical Weather Outlook."""
    html = fetch_text("https://www.nhc.noaa.gov/text/MIATWOAT.shtml")
    m = re.search(r'<pre[^>]*>(.*?)</pre>', html, re.DOTALL)
    if m:
        return re.sub(r'<[^>]+>', '', m.group(1)).strip()
    return "Tropical Weather Outlook data unavailable. Visit nhc.noaa.gov for current status."

def fetch_gulf_stream():
    """Extract Gulf Stream position lines from MFL Coastal Waters Forecast."""
    cwf = fetch_cwf("MFL")
    m = re.search(r'Gulf Stream.*?(?=\$\$)', cwf, re.DOTALL | re.IGNORECASE)
    if m:
        return [l.strip() for l in m.group(0).split('\n') if l.strip()]
    return []

# ── City definitions ──────────────────────────────────────────────────────────

CITIES = [
    # marine_lat/lon = approximate offshore coords used for Open-Meteo Marine wave model fallback
    # wave_buoys    = ordered list of alternate NDBC buoys to try for wave data when primary has no wave sensor
    {"name": "Portland",        "state": "MAINE",          "code": "KPWM", "lat": "43.6591", "lon": "-70.2568", "site": "GYX", "lat_d": "43.6°N", "buoy": "44007", "marine_lat": 43.5,  "marine_lon": -70.2},
    {"name": "Boston",          "state": "MASSACHUSETTS",  "code": "KBOS", "lat": "42.3601", "lon": "-71.0589", "site": "BOX", "lat_d": "42.4°N", "buoy": "44013", "marine_lat": 42.3,  "marine_lon": -70.7},
    {"name": "Newport",         "state": "RHODE ISLAND",   "code": "KUUU", "lat": "41.5321", "lon": "-71.2815", "site": "BOX", "lat_d": "41.5°N", "buoy": "44008", "marine_lat": 40.5,  "marine_lon": -69.4},
    {"name": "New York",        "state": "NEW YORK",       "code": "KJFK", "lat": "40.6413", "lon": "-73.7781", "site": "OKX", "lat_d": "40.6°N", "buoy": "44025", "marine_lat": 40.3,  "marine_lon": -73.2},
    {"name": "Chesapeake Bay",  "state": "MARYLAND",       "code": "KNHK", "lat": "38.2840", "lon": "-76.4110", "site": "LWX", "lat_d": "38.3°N", "buoy": "CHLV2", "wave_buoys": ["44064", "44014"], "marine_lat": 37.0, "marine_lon": -75.0, "windy_lat": 37.0, "windy_lon": -75.0},
    {"name": "Norfolk",         "state": "VIRGINIA",       "code": "KORF", "lat": "36.9076", "lon": "-76.0179", "site": "AKQ", "lat_d": "36.9°N", "buoy": "44014", "marine_lat": 36.6,  "marine_lon": -74.9},
    {"name": "Outer Banks",     "state": "NORTH CAROLINA", "code": "KHSE", "lat": "35.2330", "lon": "-75.6177", "site": "MHX", "lat_d": "35.2°N", "buoy": "41025", "marine_lat": 35.0,  "marine_lon": -75.4},
    {"name": "Charleston",      "state": "SOUTH CAROLINA", "code": "KCHS", "lat": "32.8986", "lon": "-80.0407", "site": "CHS", "lat_d": "32.9°N", "buoy": "41004", "marine_lat": 32.5,  "marine_lon": -79.1},
    {"name": "Jacksonville",    "state": "FLORIDA",        "code": "KNIP", "lat": "30.3322", "lon": "-81.6557", "site": "JAX", "lat_d": "30.2°N", "buoy": "41008", "marine_lat": 31.4,  "marine_lon": -80.9},
    {"name": "Fort Lauderdale", "state": "FLORIDA",        "code": "KFXE", "lat": "26.1224", "lon": "-80.1373", "site": "MFL", "lat_d": "26.1°N", "buoy": "FWYF1", "marine_lat": 26.0,  "marine_lon": -79.5, "windy_lat": 26.0, "windy_lon": -79.5},
    {"name": "Miami",           "state": "FLORIDA",        "code": "KMIA", "lat": "25.7617", "lon": "-80.1918", "site": "MFL", "lat_d": "25.8°N", "buoy": "FWYF1", "marine_lat": 25.7,  "marine_lon": -79.5, "windy_lat": 25.7, "windy_lon": -79.5},
    {"name": "Key West",        "state": "FLORIDA",        "code": "KEYW", "lat": "24.5561", "lon": "-81.7595", "site": "KEY", "lat_d": "24.6°N", "buoy": "SMKF1", "marine_lat": 24.5,  "marine_lon": -81.5, "windy_lat": 24.5, "windy_lon": -81.5},
]

REGIONS = [
    ("northeast",   "🧭 Northeast",   "Maine · Rhode Island · Massachusetts",                    ["Portland", "Boston", "Newport"]),
    ("midatlantic", "🌊 Mid-Atlantic", "New York · Chesapeake Bay · Virginia",                   ["New York", "Chesapeake Bay", "Norfolk"]),
    ("southeast",   "🌴 Southeast",   "North Carolina · South Carolina · NE Florida",            ["Outer Banks", "Charleston", "Jacksonville"]),
    ("florida",     "🌴 Florida",     "Fort Lauderdale · Miami · Key West",                      ["Fort Lauderdale", "Miami", "Key West"]),
]

# ── HTML builder ──────────────────────────────────────────────────────────────

def period_html(p):
    name = p.get("name", "")
    temp = p.get("temp", "")
    desc = p.get("desc", "")
    day_kws = ["afternoon","day","monday","tuesday","wednesday","thursday","friday","saturday","sunday","today","tonight"]
    temp_cls = "temp-hi" if any(x in name.lower() for x in day_kws) else "temp-lo"
    return f"""
      <div class="period">
        <div class="period-name">{name}</div>
        <div class="period-temp {temp_cls}">{temp}</div>
        <div class="period-desc">{desc}</div>
      </div>"""

def city_card_html(city, data):
    periods = "".join(period_html(p) for p in data["periods"][:6])
    nws_url = (f"https://forecast.weather.gov/MapClick.php"
               f"?CityName={city['name'].replace(' ','+')}"
               f"&state={city['state'].split()[0]}"
               f"&site={city['site']}"
               f"&textField1={city['lat']}&textField2={city['lon']}")
    hi_badge = (f'<span class="heat">&nbsp;HI {data["heat_index"]}&nbsp;</span>'
                if data["heat_index"] else "")
    home_badge = ' · Home Port' if city["name"] == "Fort Lauderdale" else ""
    return f"""
  <div class="city-card">
    <div class="city-card-header">
      <div>
        <div class="city-name">{city['name']}</div>
        <div class="city-state">{city['state']} · {city['code']} · {city['lat_d']}{home_badge}</div>
      </div>
      <div class="current-temp">
        <div class="temp-val">{data['temp']}</div>
        <div class="temp-label">Current</div>
      </div>
    </div>
    <div class="current-row">
      <div class="curr-item">🌤 {data['conditions']}</div>
      <div class="curr-item">💧 Humidity: <span>{data['humidity']}</span></div>
      <div class="curr-item">💨 Wind: <span>{data['wind']}</span></div>
      <div class="curr-item">👁 Vis: <span>{data['visibility']}</span></div>
      <div class="curr-item">🌊 Waves: <span>{data['wave_height']}</span></div>
      {hi_badge}
    </div>
    <div class="forecast-list">
      <h4>48-Hour Forecast (NWS {city['site']})</h4>
      {periods if periods else '<p style="color:var(--muted);font-size:0.82rem;">Forecast data temporarily unavailable — visit NWS for full forecast.</p>'}
    </div>
    <div class="card-footer">
      <span>NWS {city['site']} · {city['code']}</span>
      <a href="{nws_url}" target="_blank">Full Forecast ↗</a>
    </div>
  </div>"""

CSS = """
:root{--navy:#0d2340;--blue:#1a4a7a;--teal:#0e7490;--gold:#ca8a04;--red:#dc2626;--green:#15803d;--bg:#f0f4f8;--card:#ffffff;--border:#cbd5e1;--text:#1e293b;--muted:#64748b;--warn:#7c2d12;--warn-bg:#fff7ed;}
*{box-sizing:border-box;margin:0;padding:0;}
body{font-family:'Segoe UI',Arial,sans-serif;background:var(--bg);color:var(--text);line-height:1.5;}
header{background:linear-gradient(135deg,var(--navy) 0%,var(--blue) 100%);color:#fff;padding:0;}
.header-top{display:flex;align-items:center;justify-content:space-between;padding:18px 32px 12px;border-bottom:2px solid var(--teal);}
.site-brand{display:flex;align-items:center;gap:14px;}
.anchor-icon{font-size:2.4rem;}
.brand-name{font-size:1.6rem;font-weight:700;}
.brand-sub{font-size:0.8rem;color:#93c5fd;letter-spacing:1px;text-transform:uppercase;}
.report-meta{text-align:right;font-size:0.82rem;color:#93c5fd;}
.report-meta strong{color:#fff;font-size:0.9rem;}
.issue-banner{background:#0a3d5c;color:#fff;text-align:center;padding:12px 20px;display:flex;justify-content:center;align-items:center;gap:40px;flex-wrap:wrap;border-bottom:3px solid var(--gold);}
.ib-item{display:flex;flex-direction:column;align-items:center;gap:2px;}
.ib-label{font-size:0.68rem;text-transform:uppercase;letter-spacing:1.2px;color:#7dd3fc;font-weight:700;}
.ib-val{font-size:1.1rem;font-weight:800;color:#fff;letter-spacing:0.3px;}
.ib-sep{width:1px;height:40px;background:rgba(255,255,255,0.2);}
.header-title{padding:16px 32px 20px;text-align:center;}
.header-title h1{font-size:1.8rem;font-weight:700;margin-bottom:4px;}
.header-title p{color:#93c5fd;font-size:0.92rem;}
nav{background:var(--teal);display:flex;gap:0;overflow-x:auto;padding:0 16px;}
nav a{color:#e0f2fe;text-decoration:none;padding:8px 16px;font-size:0.82rem;font-weight:600;white-space:nowrap;border-bottom:3px solid transparent;transition:border-color 0.15s;}
nav a:hover{border-bottom-color:#fff;color:#fff;}
.alert-banner{background:var(--warn-bg);border-left:5px solid var(--red);margin:20px 24px;padding:14px 18px;border-radius:0 6px 6px 0;}
.alert-banner .alert-title{font-weight:700;color:var(--warn);font-size:0.95rem;margin-bottom:4px;}
.alert-banner p{color:#7c3415;font-size:0.88rem;}
main{max-width:1280px;margin:0 auto;padding:16px 20px 40px;}
.overview-card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:20px 24px;margin-bottom:24px;box-shadow:0 1px 4px rgba(0,0,0,0.07);}
.overview-card h2{font-size:1.1rem;color:var(--navy);margin-bottom:12px;padding-bottom:8px;border-bottom:2px solid var(--teal);display:flex;align-items:center;gap:8px;}
.overview-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;}
.overview-item{background:#f1f5f9;border-radius:8px;padding:12px 14px;border-left:4px solid var(--teal);}
.overview-item .ov-region{font-size:0.75rem;font-weight:700;text-transform:uppercase;color:var(--teal);letter-spacing:0.5px;}
.overview-item .ov-summary{font-size:0.87rem;color:var(--text);margin-top:3px;}
.section-header{display:flex;align-items:center;gap:10px;margin:24px 0 14px;}
.section-header h2{font-size:1.25rem;color:var(--navy);font-weight:700;}
.section-line{flex:1;height:2px;background:var(--border);}
.region-flag{background:var(--navy);color:#fff;font-size:0.72rem;font-weight:700;padding:3px 10px;border-radius:20px;text-transform:uppercase;letter-spacing:0.5px;}
.city-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:18px;margin-bottom:8px;}
.city-card{background:var(--card);border:1px solid var(--border);border-radius:10px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,0.07);}
.city-card-header{background:linear-gradient(90deg,var(--navy) 0%,var(--blue) 100%);color:#fff;padding:12px 16px;display:flex;justify-content:space-between;align-items:center;}
.city-name{font-size:1.1rem;font-weight:700;}
.city-state{font-size:0.78rem;color:#93c5fd;letter-spacing:0.5px;}
.current-temp{text-align:right;}
.temp-val{font-size:2rem;font-weight:700;line-height:1;}
.temp-label{font-size:0.72rem;color:#93c5fd;}
.current-row{display:flex;flex-wrap:wrap;gap:6px;padding:10px 14px;border-bottom:1px solid var(--border);background:#f8fafc;}
.curr-item{font-size:0.8rem;color:var(--muted);}
.curr-item span{color:var(--text);font-weight:600;}
.heat{background:#fef3c7;color:#92400e;font-size:0.75rem;font-weight:700;padding:2px 7px;border-radius:12px;}
.forecast-list{padding:12px 14px;}
.forecast-list h4{font-size:0.78rem;text-transform:uppercase;color:var(--muted);letter-spacing:0.5px;margin-bottom:8px;}
.period{display:grid;grid-template-columns:110px 55px 1fr;gap:6px;padding:7px 0;border-bottom:1px solid #f1f5f9;font-size:0.83rem;align-items:start;}
.period:last-child{border-bottom:none;}
.period-name{font-weight:600;color:var(--navy);}
.temp-hi{color:#dc2626;font-weight:700;}
.temp-lo{color:#2563eb;font-weight:700;}
.period-desc{color:var(--text);}
.card-footer{display:flex;justify-content:space-between;align-items:center;padding:8px 14px;background:#f8fafc;border-top:1px solid var(--border);font-size:0.75rem;color:var(--muted);}
.card-footer a{color:var(--teal);font-weight:600;text-decoration:none;}
.mariner-box{background:linear-gradient(135deg,var(--navy) 0%,#1a3a5c 100%);color:#fff;border-radius:12px;padding:24px 28px;margin-top:28px;}
.mariner-box h3{font-size:1.15rem;font-weight:700;margin-bottom:16px;padding-bottom:10px;border-bottom:2px solid var(--teal);}
.mariner-notes{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px;}
.mariner-note{background:rgba(255,255,255,0.08);border-radius:8px;border-left:3px solid var(--teal);padding:10px 14px;font-size:0.85rem;}
.mariner-note strong{color:#7dd3fc;display:block;margin-bottom:3px;font-size:0.8rem;}
.status-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:14px;margin-bottom:20px;}
.status-card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:16px 18px;box-shadow:0 1px 4px rgba(0,0,0,0.07);}
.status-card.all-clear{border-left:5px solid var(--green);}
.status-card.caution{border-left:5px solid var(--gold);}
.status-card.info{border-left:5px solid var(--teal);}
.status-title{font-weight:700;font-size:0.77rem;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px;}
.status-card.all-clear .status-title{color:var(--green);}
.status-card.caution .status-title{color:var(--gold);}
.status-card.info .status-title{color:var(--teal);}
.status-value{font-size:1.35rem;font-weight:700;color:var(--text);line-height:1.2;margin-bottom:4px;}
.status-sub{font-size:0.79rem;color:var(--muted);line-height:1.5;}
.nhc-block{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:20px 24px;margin-bottom:20px;box-shadow:0 1px 4px rgba(0,0,0,0.07);}
.nhc-block h2{font-size:1.1rem;color:var(--navy);margin-bottom:12px;padding-bottom:8px;border-bottom:2px solid var(--teal);}
.nhc-text{font-size:0.87rem;font-family:monospace;white-space:pre-wrap;color:var(--text);line-height:1.7;max-height:400px;overflow-y:auto;}
.gulf-stream-box{background:linear-gradient(135deg,#0a1f3a 0%,#0c6682 100%);color:#fff;border-radius:10px;padding:18px 22px;margin-bottom:20px;display:grid;grid-template-columns:auto 1fr;gap:18px;align-items:start;}
.gs-icon{font-size:2.4rem;}
.gs-title{font-size:0.77rem;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;color:#7dd3fc;margin-bottom:6px;}
.gs-positions{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:8px;margin-top:10px;}
.gs-pos{background:rgba(255,255,255,0.12);border-radius:6px;padding:8px 12px;font-size:0.82rem;}
.gs-pos strong{color:#93c5fd;display:block;font-size:0.72rem;}
.gs-warn{font-size:0.79rem;color:#fcd34d;margin-top:10px;border-top:1px solid rgba(255,255,255,0.2);padding-top:8px;}
footer{background:var(--navy);color:#93c5fd;text-align:center;padding:20px;font-size:0.8rem;margin-top:32px;}
footer a{color:#7dd3fc;}
footer strong{color:#fff;}
.buoy-table{width:100%;border-collapse:collapse;background:var(--card);border-radius:10px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,0.07);font-size:0.84rem;margin-bottom:8px;}
.buoy-table thead tr{background:linear-gradient(90deg,#0a2540,#0c5c7a);}
.buoy-table th{color:#bae6fd;font-size:0.70rem;text-transform:uppercase;letter-spacing:0.6px;padding:8px 12px;text-align:left;font-weight:700;white-space:nowrap;}
.buoy-table td{padding:6px 12px;border-bottom:1px solid #f1f5f9;vertical-align:middle;white-space:nowrap;}
.buoy-table tr:last-child td{border-bottom:none;}
.buoy-table tr:nth-child(even) td{background:#f8fafc;}
.buoy-table .grp-row td{background:#0a3d5c;color:#7dd3fc;font-size:0.68rem;font-weight:700;letter-spacing:1px;padding:3px 12px;text-transform:uppercase;}
.bloc{font-weight:700;color:var(--navy);font-size:0.84rem;}
.bid{font-size:0.70rem;color:var(--muted);}
.bwv{font-weight:800;color:#0e7490;font-size:0.92rem;}
.sea-calm{background:#dcfce7;color:#15803d;}
.sea-moderate{background:#fef9c3;color:#a16207;}
.sea-rough{background:#fee2e2;color:#dc2626;}
.sea-very-rough{background:#7f1d1d;color:#fecaca;}
.bsea{display:inline-block;padding:2px 8px;border-radius:8px;font-size:0.68rem;font-weight:700;}
.trend-up{color:#dc2626;font-weight:700;}
.trend-down{color:#15803d;font-weight:700;}
.trend-steady{color:#64748b;font-weight:700;}
.ndbc-lnk{color:var(--teal);text-decoration:none;font-size:0.70rem;font-weight:600;}
@media(max-width:600px){.city-grid{grid-template-columns:1fr;}.header-top{flex-direction:column;gap:8px;text-align:center;}.buoy-table{font-size:0.72rem;}}
"""

OVERVIEW_REGIONS = [
    ("🧭", "Maine / New England",        ["Portland", "Boston", "Newport"]),
    ("🌊", "Mid-Atlantic",               ["New York", "Chesapeake Bay", "Norfolk"]),
    ("🌴", "Southeast / NE Florida",     ["Outer Banks", "Charleston", "Jacksonville"]),
    ("🌴", "South Florida / Keys",       ["Fort Lauderdale", "Miami", "Key West"]),
]

def overview_item(icon, label, city_names, city_data):
    lines = []
    for cn in city_names:
        d = city_data.get(cn, {})
        temp = d.get("temp", "N/A")
        cond = d.get("conditions", "N/A")
        wind = d.get("wind", "N/A")
        next_period = d.get("periods", [{}])[0] if d.get("periods") else {}
        next_name = next_period.get("name", "")
        next_desc = next_period.get("desc", "")
        # Trim detailed forecast to ~80 chars for the overview
        if len(next_desc) > 80:
            next_desc = next_desc[:77].rsplit(" ", 1)[0] + "…"
        summary = f"<strong>{cn}:</strong> {temp} · {cond} · Wind {wind}"
        if next_name and next_desc:
            summary += f"<br><em>{next_name}:</em> {next_desc}"
        lines.append(summary)
    body = "<br><br>".join(lines)
    return f'<div class="overview-item"><div class="ov-region">{icon} {label}</div><div class="ov-summary">{body}</div></div>'

BUOY_REGIONS = [
    ("🧭 Northeast",    ["Portland", "Boston", "Newport"]),
    ("🌊 Mid-Atlantic", ["New York", "Chesapeake Bay", "Norfolk"]),
    ("🌴 Southeast",    ["Outer Banks", "Charleston", "Jacksonville"]),
    ("🌴 Florida",      ["Fort Lauderdale", "Miami", "Key West"]),
]

def build_buoy_table(buoy_data):
    city_map = {c["name"]: c for c in CITIES}
    rows = ""
    for region_label, names in BUOY_REGIONS:
        rows += f'<tr class="grp-row"><td colspan="10">{region_label}</td></tr>\n'
        for cn in names:
            bd = buoy_data.get(cn, {})
            city = city_map.get(cn, {})
            buoy_id = city.get("buoy", "")
            wave_source = bd.get("wave_source", "primary")
            ndbc_url    = f"https://www.ndbc.noaa.gov/station_page.php?station={buoy_id}"
            if wave_source == "windy":
                windy_lat = city.get("windy_lat", 0)
                windy_lon = city.get("windy_lon", 0)
                station_cell = (f'<div class="bid">{buoy_id}</div>'
                                f'<div class="bid" style="color:#0e7490;font-weight:700;">Waves: Windy/ECMWF</div>')
                link_cell    = (f'<a class="ndbc-lnk" href="{ndbc_url}" target="_blank">NDBC ↗</a> '
                                f'<a class="ndbc-lnk" href="https://www.windy.com/{windy_lat}/{windy_lon}?waves" target="_blank">Windy ↗</a>')
            elif wave_source == "openmeteo":
                station_cell = (f'<div class="bid">{buoy_id}</div>'
                                f'<div class="bid" style="color:#0e7490;font-weight:700;">Waves: Open-Meteo</div>')
                link_cell    = (f'<a class="ndbc-lnk" href="{ndbc_url}" target="_blank">NDBC ↗</a> '
                                f'<a class="ndbc-lnk" href="https://open-meteo.com" target="_blank">OM ↗</a>')
            elif wave_source and wave_source.startswith("buoy:"):
                alt_id       = wave_source.split(":", 1)[1]
                alt_url      = f"https://www.ndbc.noaa.gov/station_page.php?station={alt_id}"
                station_cell = (f'<div class="bid">{buoy_id}</div>'
                                f'<div class="bid" style="color:#0e7490;font-weight:700;">Waves: {alt_id}</div>')
                link_cell    = (f'<a class="ndbc-lnk" href="{ndbc_url}" target="_blank">NDBC ↗</a> '
                                f'<a class="ndbc-lnk" href="{alt_url}" target="_blank">{alt_id} ↗</a>')
            else:
                station_cell = f'<div class="bid">{buoy_id}</div>'
                link_cell    = f'<a class="ndbc-lnk" href="{ndbc_url}" target="_blank">↗</a>'
            sea_label = bd.get("sea_label", "N/A")
            sea_cls   = bd.get("sea_cls", "sea-calm")
            t_arrow   = bd.get("trend", "→")
            t_label   = bd.get("trend_label", "Steady")
            t_cls     = bd.get("trend_cls", "trend-steady")
            rows += f"""<tr>
  <td><div class="bloc">{cn}</div></td>
  <td>{station_cell}</td>
  <td class="bwv">{bd.get("wvht","N/A")}</td>
  <td>{bd.get("dpd","N/A")}</td>
  <td>{bd.get("mwd","N/A")}</td>
  <td>{bd.get("wspd","N/A")}</td>
  <td>{bd.get("wtmp","N/A")}</td>
  <td>{bd.get("pres","N/A")}</td>
  <td><span class="bsea {sea_cls}">{sea_label}</span></td>
  <td><span class="{t_cls}">{t_arrow} {t_label}</span></td>
  <td>{link_cell}</td>
</tr>"""
    return f"""
<div class="section-header" id="buoys">
  <span class="region-flag">📡 NDBC Buoy Data</span>
  <h2>Offshore Buoy Observations — Nearest Station · Live NDBC Data</h2>
  <div class="section-line"></div>
</div>
<table class="buoy-table">
  <thead><tr>
    <th>Location</th><th>Station</th><th>🌊 Wave Ht</th><th>Period</th>
    <th>Direction</th><th>💨 Wind</th><th>🌡 Water</th><th>Pressure</th>
    <th>Sea State</th><th>24hr Trend</th><th></th>
  </tr></thead>
  <tbody>{rows}</tbody>
</table>
<p style="font-size:0.72rem;color:var(--muted);margin-bottom:24px;">
  Sea state: Calm &lt;2ft · Slight 2–4ft · Moderate 4–8ft · Rough 8–13ft · Very Rough 13ft+ &nbsp;|&nbsp; Trend based on 24-hr comparison &nbsp;|&nbsp;
  Sources: <a href="https://www.ndbc.noaa.gov" target="_blank">NOAA NDBC</a> (buoy obs) · <a href="https://marine-api.open-meteo.com" target="_blank">Open-Meteo Marine</a> (wave model fallback) · <a href="https://www.windy.com" target="_blank">Windy/ECMWF</a> (wave model, where key set)
</p>"""

def build_html(city_data, buoy_data, nhc_two, gs_lines):
    today_str = now.strftime("%A, %B %-d")

    # Overview boxes
    overview_html = "\n    ".join(
        overview_item(icon, label, names, city_data)
        for icon, label, names in OVERVIEW_REGIONS
    )

    # Buoy table
    buoy_table_html = build_buoy_table(buoy_data)

    # City sections
    sections_html = ""
    for region_id, region_label, region_title, city_names in REGIONS:
        cards = ""
        for cn in city_names:
            city = next(c for c in CITIES if c["name"] == cn)
            d = city_data.get(cn, {
                "temp": "N/A", "conditions": "N/A", "humidity": "N/A",
                "wind": "N/A", "visibility": "N/A", "heat_index": "", "wave_height": "N/A", "periods": []
            })
            cards += city_card_html(city, d)
        sections_html += f"""
<div class="section-header" id="{region_id}">
  <span class="region-flag">{region_label}</span>
  <h2>{region_title}</h2>
  <div class="section-line"></div>
</div>
<div class="city-grid">{cards}</div>
"""

    # Gulf Stream positions
    gs_pos_html = ""
    for line in (gs_lines[2:6] if len(gs_lines) > 2 else []):
        parts = line.strip().split(" of ")
        if len(parts) == 2:
            gs_pos_html += (f'<div class="gs-pos">'
                            f'<strong>{parts[1].strip()}</strong>'
                            f'{parts[0].strip()}</div>')
    if not gs_pos_html:
        gs_pos_html = ('<div class="gs-pos"><strong>Off Port Everglades</strong>~19 nm SE (typical)</div>'
                       '<div class="gs-pos"><strong>Off Jupiter Inlet</strong>~17 nm E (typical)</div>')

    # NHC full text
    nhc_display = nhc_two if nhc_two else "Tropical Weather Outlook data unavailable."

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<meta http-equiv="Pragma" content="no-cache">
<meta http-equiv="Expires" content="0">
<title>East Coast 48-Hour Maritime Weather Report — Captain Georgia</title>
<style>{CSS}</style>
</head>
<body>
<header>
  <div class="header-top">
    <div class="site-brand">
      <span class="anchor-icon">⚓</span>
      <div>
        <div class="brand-name">Captain Georgia</div>
        <div class="brand-sub">USCG Master · OICNW · Fort Lauderdale, FL</div>
      </div>
    </div>
    <div class="report-meta">
      <strong>East Coast 48-Hour Maritime Weather Report</strong><br>
      Issued: {ISSUE_TIME}<br>
      Source: NOAA / National Weather Service<br>
      Valid through: {VALID_THRU}
    </div>
  </div>
  <div class="header-title">
    <h1>🌊 East Coast Marine &amp; Coastal Weather — 48-Hour Outlook</h1>
    <p>Portland ME · Boston MA · New York NY · Norfolk VA · Charleston SC · Jacksonville FL · Fort Lauderdale FL · Miami FL</p>
  </div>
  <nav>
    <a href="#northeast">Northeast</a>
    <a href="#midatlantic">Mid-Atlantic</a>
    <a href="#southeast">Southeast</a>
    <a href="#florida">Florida</a>
    <a href="#buoys">Buoy Data</a>
    <a href="#tropics">Tropics &amp; Offshore</a>
    <a href="#mariner">Mariner Notes</a>
    <a href="https://captaingeorgia.com/weather-analysis-1" target="_blank">Captain Georgia Weather ↗</a>
    <a href="https://captaingeorgia.com/nws-marine-forecast" target="_blank">NWS Marine Forecast ↗</a>
  </nav>
  <div class="issue-banner">
    <div class="ib-item">
      <span class="ib-label">📅 Issued</span>
      <span class="ib-val">{ISSUE_TIME}</span>
    </div>
    <div class="ib-sep"></div>
    <div class="ib-item">
      <span class="ib-label">⏱ Valid Through</span>
      <span class="ib-val">{VALID_THRU}</span>
    </div>
    <div class="ib-sep"></div>
    <div class="ib-item">
      <span class="ib-label">📡 Data Source</span>
      <span class="ib-val">NOAA / NWS / NHC — Live</span>
    </div>
  </div>
</header>

<main>

<div class="overview-card">
  <h2>🗺️ Regional Overview — Next 48 Hours · Generated {today_str} at 03:00 EDT</h2>
  <div class="overview-grid">
    {overview_html}
  </div>
</div>

{buoy_table_html}

{sections_html}

<!-- TROPICS & OFFSHORE -->
<div class="section-header" id="tropics">
  <span class="region-flag">🌀 Offshore &amp; Tropics</span>
  <h2>Offshore Conditions · Tropics · Gulf Stream · Significant Systems</h2>
  <div class="section-line"></div>
</div>

<div class="status-grid">
  <div class="status-card all-clear">
    <div class="status-title">🌀 Atlantic Tropics Status</div>
    <div class="status-value">SEE NHC BELOW</div>
    <div class="status-sub">NHC Tropical Weather Outlook — current as of {ISSUE_TIME}</div>
  </div>
  <div class="status-card info">
    <div class="status-title">🌊 Gulf Stream</div>
    <div class="status-value">NORMAL</div>
    <div class="status-sub">Position data from NWS Miami (MFL) Coastal Waters Forecast</div>
  </div>
</div>

<div class="nhc-block">
  <h2>🌀 NHC Atlantic Tropical Weather Outlook</h2>
  <div class="nhc-text">{nhc_display}</div>
  <p style="font-size:0.77rem;color:var(--muted);margin-top:10px;">Source: <a href="https://www.nhc.noaa.gov/text/MIATWOAT.shtml" target="_blank">NHC — nhc.noaa.gov</a></p>
</div>

<div class="gulf-stream-box">
  <div class="gs-icon">🌊</div>
  <div>
    <div class="gs-title">Gulf Stream — West Wall Position · Source: NWS Miami (MFL) Coastal Waters Forecast</div>
    <p style="font-size:0.87rem;margin-bottom:8px;color:#e2e8f0;">The Gulf Stream west wall marks the inshore boundary of the northward-flowing current (avg 2–4 kt). <strong style="color:#7dd3fc;">No Gulf Stream hazards currently posted.</strong></p>
    <div class="gs-positions">{gs_pos_html}</div>
    <div class="gs-warn">⚠ Winds opposing the Gulf Stream current create steep, confused seas. Avoid strong north winds while transiting. Stream averages 2–4 kt northward flow in this region.</div>
  </div>
</div>

<!-- MARINER NOTES -->
<div class="mariner-box" id="mariner">
  <h3>⚓ Mariner's Summary — Captain Georgia · USCG Master / OICNW</h3>
  <div class="mariner-notes">
    <div class="mariner-note">
      <strong>🌊 Offshore Conditions</strong>
      Review individual city forecasts above for current sea state. Monitor NWS Offshore Forecasts for zones beyond 60 nm. Seas and winds higher in and near thunderstorms.
    </div>
    <div class="mariner-note">
      <strong>⛈ Florida Operations</strong>
      Daily afternoon sea-breeze thunderstorms typical June–September. Plan departures before 1100 local. Seas under 2 ft in Straits of Florida under normal summer pattern. Check Biscayne Bay and ICW conditions before transiting.
    </div>
    <div class="mariner-note">
      <strong>🌊 Gulf Stream — SE FL</strong>
      Stream is 15–22 nm offshore. Favorable 2–4 kt northward set when crossing eastbound. Caution: opposing winds cause dangerous, steep seas. Never cross against a northerly above 15 kt. Check position data from Naval Oceanographic Office.
    </div>
    <div class="mariner-note">
      <strong>🌀 Tropics Watch</strong>
      Atlantic hurricane season: June 1 – November 30. Check NHC Tropical Weather Outlook daily while at sea. Monitor VHF WX channels 1–7. For offshore passages &gt;30 nm monitor SSB 4125 / 6215 / 8291 kHz.
    </div>
    <div class="mariner-note">
      <strong>📻 Communications Protocol</strong>
      VHF Ch 16 continuous watch underway. NOAA WX radio: WX1–WX7. File a float plan. Report observed weather to nearest USCG station. This report auto-generates daily at 0300 EDT from live NOAA/NWS data.
    </div>
    <div class="mariner-note">
      <strong>⚓ Captain Georgia</strong>
      USCG Master · OICNW · Fort Lauderdale, FL. Full weather resources: <a href="https://captaingeorgia.com" style="color:#7dd3fc;">captaingeorgia.com</a>. Always verify with official NWS products and VHF WX before getting underway.
    </div>
  </div>
</div>

</main>

<footer>
  <p><strong>Captain Georgia</strong> — USCG Master · OICNW · Fort Lauderdale, FL</p>
  <p style="margin-top:6px">Data sourced from <a href="https://www.weather.gov" target="_blank">NOAA National Weather Service</a> &amp; <a href="https://www.nhc.noaa.gov" target="_blank">National Hurricane Center</a></p>
  <p style="margin-top:4px">Auto-generated daily at 03:00 EDT · Always consult official NWS products and VHF WX radio before underway · <a href="https://captaingeorgia.com" target="_blank">captaingeorgia.com</a></p>
</footer>
</body>
</html>"""

# ── main ──────────────────────────────────────────────────────────────────────

def main():
    print("Fetching NWS city data via JSON API...", file=sys.stderr)
    city_data = {}
    buoy_data = {}
    for city in CITIES:
        print(f"  {city['name']} ({city['code']})...", file=sys.stderr)
        d = fetch_nws_api(city["lat"], city["lon"], city["code"])
        print(f"    Buoy {city['buoy']}...", file=sys.stderr)
        bd = fetch_buoy_full(city["buoy"])
        bd["wave_source"] = "primary"

        # ── Fallback chain for all buoy fields ───────────────────────────────
        # Step 1: Try alternate NDBC buoys for ANY missing data (waves AND met)
        if city.get("wave_buoys") and (bd["wvht"] == "N/A" or bd["wspd"] == "N/A" or bd["pres"] == "N/A" or bd["wtmp"] == "N/A"):
            for alt_id in city["wave_buoys"]:
                print(f"    → Missing data on {city['buoy']}, trying alt buoy {alt_id}...", file=sys.stderr)
                alt = fetch_buoy_full(alt_id)
                # Fill in any N/A fields from the alt buoy
                if bd["wvht"] == "N/A" and alt["wvht"] != "N/A":
                    for k in ["wvht","dpd","mwd","sea_label","sea_cls","trend","trend_label","trend_cls"]:
                        bd[k] = alt[k]
                    bd["wave_source"] = f"buoy:{alt_id}"
                if bd["wspd"] == "N/A" and alt["wspd"] != "N/A":
                    bd["wspd"] = alt["wspd"]
                if bd["wtmp"] == "N/A" and alt["wtmp"] != "N/A":
                    bd["wtmp"] = alt["wtmp"]
                if bd["pres"] == "N/A" and alt["pres"] != "N/A":
                    bd["pres"] = alt["pres"]
                print(f"    ✓ Gap-filled from {alt_id}: wave={bd['wvht']} wind={bd['wspd']} wtmp={bd['wtmp']} pres={bd['pres']}", file=sys.stderr)
                # Stop if all key fields are now filled
                if bd["wvht"] != "N/A" and bd["wspd"] != "N/A" and bd["wtmp"] != "N/A" and bd["pres"] != "N/A":
                    break

        # Step 2: Open-Meteo Marine (free, no key required) — covers any remaining gap
        if bd["wvht"] == "N/A" and city.get("marine_lat"):
            print(f"    → Wave still N/A, trying Open-Meteo Marine ({city['marine_lat']},{city['marine_lon']})...", file=sys.stderr)
            om = fetch_openmeteo_marine(city["marine_lat"], city["marine_lon"])
            if om:
                bd.update(om)
                bd["wave_source"] = "openmeteo"
                print(f"    ✓ Wave data from Open-Meteo Marine", file=sys.stderr)

        # Step 3: Windy ECMWF (higher-res model, requires paid API key)
        if bd["wvht"] == "N/A" and city.get("windy_lat") and WINDY_API_KEY:
            print(f"    → Trying Windy at ({city['windy_lat']},{city['windy_lon']})...", file=sys.stderr)
            windy = fetch_windy_waves(city["windy_lat"], city["windy_lon"])
            if windy:
                bd.update(windy)
                bd["wave_source"] = "windy"
                print(f"    ✓ Wave data from Windy/ECMWF", file=sys.stderr)

        d["wave_height"] = bd["wvht"]
        city_data[city["name"]] = d
        buoy_data[city["name"]] = bd
        time.sleep(0.5)   # polite rate limiting

    print("Fetching NHC Tropical Weather Outlook...", file=sys.stderr)
    nhc_two = fetch_nhc_two()

    print("Fetching Gulf Stream position...", file=sys.stderr)
    gs_lines = fetch_gulf_stream()

    print("Building HTML...", file=sys.stderr)
    html = build_html(city_data, buoy_data, nhc_two, gs_lines)

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("Done — index.html written.", file=sys.stderr)

if __name__ == "__main__":
    main()
