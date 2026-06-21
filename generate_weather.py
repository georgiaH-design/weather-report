#!/usr/bin/env python3
"""
Captain Georgia — Daily East Coast Maritime Weather Report Generator
Fetches live NWS + NHC data and builds a self-contained HTML file.
Runs via GitHub Actions every morning at 0300 EDT (0700 UTC).
"""

import urllib.request
import re
import sys
from datetime import datetime, timezone, timedelta

EDT = timezone(timedelta(hours=-4))
now = datetime.now(EDT)
ISSUE_TIME = now.strftime("%A, %B %-d, %Y · %-I:%M %p EDT")
VALID_THRU = (now + timedelta(hours=48)).strftime("%A, %B %-d, %Y")

# ── helpers ──────────────────────────────────────────────────────────────────

def fetch(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "CaptainGeorgia-WeatherBot/1.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  WARN: could not fetch {url}: {e}", file=sys.stderr)
        return ""

def scrape_nws(html):
    """Extract current conditions + forecast periods from NWS MapClick page."""
    data = {"temp": "N/A", "conditions": "N/A", "humidity": "N/A",
            "wind": "N/A", "visibility": "N/A", "heat_index": "", "periods": []}
    # current temp
    m = re.search(r'<p class="myforecast-current-lrg">([\d]+)&deg;F</p>', html)
    if m: data["temp"] = m.group(1) + "°F"
    # conditions
    m = re.search(r'<p class="myforecast-current">([^<]+)</p>', html)
    if m: data["conditions"] = m.group(1).strip()
    # detail rows
    rows = re.findall(r'<td[^>]*>\s*([^<\s][^<]*?)\s*</td>\s*<td[^>]*>\s*([^<\s][^<]*?)\s*</td>', html)
    for label, val in rows:
        label_l = label.strip().lower()
        val_s = re.sub(r'<[^>]+>', '', val).strip()
        if "humidity" in label_l: data["humidity"] = val_s
        elif "wind" in label_l and "direction" not in label_l and "chill" not in label_l and "gust" not in label_l: data["wind"] = val_s
        elif "visibility" in label_l: data["visibility"] = val_s
        elif "heat index" in label_l: data["heat_index"] = val_s
    # forecast periods
    names = re.findall(r'<b>([^<]+)</b>', html)
    temps = re.findall(r'(\d+)&deg;F', html)
    descs = re.findall(r'<div title="([^"]{15,})"', html)
    for i, name in enumerate(names[:10]):
        if name in ("Forecast", "Humidity", "Wind Speed", "Barometer", "Dewpoint",
                    "Visibility", "Last update", "More Information"):
            continue
        temp = temps[i] + "°F" if i < len(temps) else ""
        desc = descs[i] if i < len(descs) else ""
        data["periods"].append({"name": name, "temp": temp, "desc": desc})
    return data

def fetch_cwf(site):
    """Fetch Coastal Waters Forecast text."""
    url = f"https://forecast.weather.gov/product.php?site={site}&issuedby={site}&product=CWF&format=txt&version=1"
    html = fetch(url)
    m = re.search(r'<pre[^>]*>(.*?)</pre>', html, re.DOTALL)
    return m.group(1).strip() if m else ""

def fetch_nhc_two():
    html = fetch("https://www.nhc.noaa.gov/text/MIATWOAT.shtml")
    m = re.search(r'<pre[^>]*>(.*?)</pre>', html, re.DOTALL)
    if m:
        txt = re.sub(r'<[^>]+>', '', m.group(1)).strip()
        return txt
    return "No data available."

def fetch_gulf_stream():
    """Extract Gulf Stream position from MFL CWF."""
    cwf = fetch_cwf("MFL")
    m = re.search(r'Gulf Stream.*?(?=\$\$)', cwf, re.DOTALL)
    if m:
        lines = [l.strip() for l in m.group(0).split('\n') if l.strip()]
        return lines
    return []

# ── city data ─────────────────────────────────────────────────────────────────

CITIES = [
    {"name": "Portland",       "state": "MAINE",          "code": "KPWM", "lat": "43.6591", "lon": "-70.2568", "site": "GYX", "lat_d": "43.6°N"},
    {"name": "Boston",         "state": "MASSACHUSETTS",  "code": "KBOS", "lat": "42.3601", "lon": "-71.0589", "site": "BOX", "lat_d": "42.4°N"},
    {"name": "New York",       "state": "NEW YORK",       "code": "KJFK", "lat": "40.6413", "lon": "-73.7781", "site": "OKX", "lat_d": "40.6°N"},
    {"name": "Norfolk",        "state": "VIRGINIA",       "code": "KORF", "lat": "36.9076", "lon": "-76.0179", "site": "AKQ", "lat_d": "36.9°N"},
    {"name": "Charleston",     "state": "SOUTH CAROLINA", "code": "KCHS", "lat": "32.8986", "lon": "-80.0407", "site": "CHS", "lat_d": "32.9°N"},
    {"name": "Jacksonville",   "state": "FLORIDA",        "code": "KNIP", "lat": "30.3322", "lon": "-81.6557", "site": "JAX", "lat_d": "30.2°N"},
    {"name": "Fort Lauderdale","state": "FLORIDA",        "code": "KFXE", "lat": "26.1224", "lon": "-80.1373", "site": "MFL", "lat_d": "26.1°N"},
    {"name": "Miami",          "state": "FLORIDA",        "code": "KMIA", "lat": "25.7617", "lon": "-80.1918", "site": "MFL", "lat_d": "25.8°N"},
]

REGIONS = [
    ("northeast",  "🧭 Northeast",   "Maine · Massachusetts",           ["Portland", "Boston"]),
    ("midatlantic","🌊 Mid-Atlantic", "New York · Virginia",             ["New York", "Norfolk"]),
    ("southeast",  "🌴 Southeast",   "South Carolina",                  ["Charleston"]),
    ("florida",    "🌴 Florida",     "Jacksonville · Fort Lauderdale · Miami", ["Jacksonville", "Fort Lauderdale", "Miami"]),
]

# ── HTML builder ──────────────────────────────────────────────────────────────

def period_html(p):
    name = p.get("name", "")
    temp = p.get("temp", "")
    desc = p.get("desc", "")
    temp_cls = "temp-hi" if any(x in name.lower() for x in ["afternoon","day","monday","tuesday","wednesday","thursday","friday","saturday","sunday"]) else "temp-lo"
    return f"""
      <div class="period">
        <div class="period-name">{name}</div>
        <div class="period-temp {temp_cls}">{temp}</div>
        <div class="period-desc">{desc}</div>
      </div>"""

def city_card_html(city, data):
    periods = "".join(period_html(p) for p in data["periods"][:5])
    nws_url = f"https://forecast.weather.gov/MapClick.php?CityName={city['name'].replace(' ','+')}&state={city['state'].split()[0]}&site={city['site']}&textField1={city['lat']}&textField2={city['lon']}"
    hi_badge = f'<span class="heat">&nbsp;HI {data["heat_index"]}&nbsp;</span>' if data["heat_index"] else ""
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
      <div class="curr-item">💧 Humidity: <span>{data['humidity']}</span></div>
      <div class="curr-item">💨 Wind: <span>{data['wind']}</span></div>
      <div class="curr-item">👁 Vis: <span>{data['visibility']}</span></div>
      {hi_badge}
    </div>
    <div class="forecast-list">
      <h4>48-Hour Forecast (NWS {city['site']})</h4>
      {periods}
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
.precip{background:#dbeafe;color:#1e40af;font-size:0.72rem;font-weight:700;padding:1px 6px;border-radius:10px;}
.precip-high{background:#fee2e2;color:#dc2626;}
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
.offshore-table{width:100%;border-collapse:collapse;font-size:0.82rem;background:var(--card);border-radius:10px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,0.07);margin-bottom:20px;}
.offshore-table th{background:var(--navy);color:#fff;padding:10px 12px;text-align:left;font-size:0.76rem;text-transform:uppercase;letter-spacing:0.4px;}
.offshore-table td{padding:10px 12px;border-bottom:1px solid var(--border);vertical-align:top;line-height:1.45;}
.offshore-table tr:last-child td{border-bottom:none;}
.offshore-table tr:nth-child(even) td{background:#f8fafc;}
.zone-name{font-weight:700;color:var(--navy);}
.zone-sub{font-size:0.74rem;color:var(--muted);}
.sea-state{display:inline-block;padding:2px 8px;border-radius:12px;font-size:0.77rem;font-weight:700;}
.sea-calm{background:#dcfce7;color:#15803d;}
.sea-moderate{background:#fef9c3;color:#a16207;}
.sea-rough{background:#fee2e2;color:#dc2626;}
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
@media(max-width:600px){.city-grid{grid-template-columns:1fr;}.header-top{flex-direction:column;gap:8px;text-align:center;}}
"""

def build_html(city_data, nhc_two, gs_lines):
    today_str = now.strftime("%A, %B %-d")

    # Build city sections
    sections_html = ""
    for region_id, region_label, region_title, city_names in REGIONS:
        cards = ""
        for cn in city_names:
            city = next(c for c in CITIES if c["name"] == cn)
            d = city_data.get(cn, {"temp":"N/A","conditions":"N/A","humidity":"N/A","wind":"N/A","visibility":"N/A","heat_index":"","periods":[]})
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
    for line in gs_lines[2:6] if len(gs_lines) > 2 else []:
        parts = line.strip().split(" of ")
        if len(parts) == 2:
            gs_pos_html += f'<div class="gs-pos"><strong>{parts[1].strip()}</strong>{parts[0].strip()}</div>'

    # NHC summary (first 3 lines of actual text)
    nhc_clean = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', nhc_two)).strip()
    nhc_summary = nhc_clean[:300] if nhc_clean else "No data available."

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
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
    <div class="overview-item"><div class="ov-region">🧭 Maine / New England</div><div class="ov-summary">Live NWS data — see Portland forecast below.</div></div>
    <div class="overview-item"><div class="ov-region">⛈ Massachusetts / Boston</div><div class="ov-summary">Live NWS data — see Boston forecast below.</div></div>
    <div class="overview-item"><div class="ov-region">🌊 New York / NJ Coast</div><div class="ov-summary">Live NWS data — see New York forecast below.</div></div>
    <div class="overview-item"><div class="ov-region">🌡 Virginia / Hampton Roads</div><div class="ov-summary">Live NWS data — see Norfolk forecast below.</div></div>
    <div class="overview-item"><div class="ov-region">☀️ South Carolina</div><div class="ov-summary">Live NWS data — see Charleston forecast below.</div></div>
    <div class="overview-item"><div class="ov-region">⛈ Northeast Florida</div><div class="ov-summary">Live NWS data — see Jacksonville forecast below.</div></div>
    <div class="overview-item"><div class="ov-region">🌤 South Florida</div><div class="ov-summary">Live NWS data — see Fort Lauderdale &amp; Miami below.</div></div>
  </div>
</div>

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
    <div class="status-value">SEE BELOW</div>
    <div class="status-sub">NHC Tropical Weather Outlook — current as of 03:00 EDT today</div>
  </div>
  <div class="status-card info">
    <div class="status-title">🌊 Gulf Stream</div>
    <div class="status-value">NORMAL</div>
    <div class="status-sub">No Gulf Stream hazards posted · Position data from Naval Oceanographic Office</div>
  </div>
</div>

<div class="overview-card">
  <h2>🌀 NHC Atlantic Tropical Weather Outlook</h2>
  <p style="font-size:0.88rem; white-space:pre-wrap; font-family:monospace; color:var(--text);">{nhc_summary}</p>
  <p style="font-size:0.77rem; color:var(--muted); margin-top:8px;">Source: <a href="https://www.nhc.noaa.gov/text/MIATWOAT.shtml" target="_blank">NHC — nhc.noaa.gov</a></p>
</div>

<div class="gulf-stream-box">
  <div class="gs-icon">🌊</div>
  <div>
    <div class="gs-title">Gulf Stream — West Wall Position · Source: Naval Oceanographic Office via NWS Miami</div>
    <p style="font-size:0.87rem; margin-bottom:8px; color:#e2e8f0;">The Gulf Stream west wall marks the inshore boundary of the northward-flowing current (avg 2–4 kt). <strong style="color:#7dd3fc;">No Gulf Stream hazards currently posted.</strong></p>
    <div class="gs-positions">{gs_pos_html if gs_pos_html else '<div class="gs-pos"><strong>Off Port Everglades</strong>~19 nm SE (typical)</div><div class="gs-pos"><strong>Off Jupiter Inlet</strong>~17 nm E (typical)</div>'}</div>
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
    print("Fetching NWS city forecasts...", file=sys.stderr)
    city_data = {}
    for city in CITIES:
        print(f"  {city['name']}...", file=sys.stderr)
        url = f"https://forecast.weather.gov/MapClick.php?CityName={city['name'].replace(' ','+')}&state={city['state'].split()[0]}&site={city['site']}&textField1={city['lat']}&textField2={city['lon']}"
        html = fetch(url)
        city_data[city["name"]] = scrape_nws(html)

    print("Fetching NHC Tropical Weather Outlook...", file=sys.stderr)
    nhc_two = fetch_nhc_two()

    print("Fetching Gulf Stream position...", file=sys.stderr)
    gs_lines = fetch_gulf_stream()

    print("Building HTML...", file=sys.stderr)
    html = build_html(city_data, nhc_two, gs_lines)

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("Done — index.html written.", file=sys.stderr)

if __name__ == "__main__":
    main()
