import asyncio
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from playwright.async_api import async_playwright

URL = "https://www.financialjuice.com/"
WEBHOOK_URL = os.environ["FOREXFACTORY_WEBHOOK_URL"]
PARIS = ZoneInfo("Europe/Paris")
DEDUP_FILE = Path("digest_last_sent.txt")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

RED_COLOR = 0xE0201B

FR_DAYS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]
FR_MONTHS = ["janv.", "fevr.", "mars", "avr.", "mai", "juin", "juil.", "aout", "sept.", "oct.", "nov.", "dec."]

ACRONYM_MAP = {
    "non-farm payrolls": "NFP",
    "non farm payrolls": "NFP",
    "nonfarm payrolls": "NFP",
    "consumer price index": "CPI",
    "producer price index": "PPI",
    "gross domestic product": "GDP",
    "purchasing managers index": "PMI",
    "purchasing manager index": "PMI",
    "federal open market committee": "FOMC",
    "institute for supply management": "ISM",
    "personal consumption expenditures": "PCE",
    "existing home sales": "EHS",
}

MARKETS = {
    "US": {"flag": "🇺🇸", "currency": "USD"},
    "EU": {"flag": "🇪🇺", "currency": "EUR"},
    "DE": {"flag": "🇩🇪", "currency": "EUR"},
    "FR": {"flag": "🇫🇷", "currency": "EUR"},
    "IT": {"flag": "🇮🇹", "currency": "EUR"},
    "ES": {"flag": "🇪🇸", "currency": "EUR"},
    "NL": {"flag": "🇳🇱", "currency": "EUR"},
    "GB": {"flag": "🇬🇧", "currency": "GBP"},
    "UK": {"flag": "🇬🇧", "currency": "GBP"},
    "JP": {"flag": "🇯🇵", "currency": "JPY"},
    "CN": {"flag": "🇨🇳", "currency": "CNY"},
    "AU": {"flag": "🇦🇺", "currency": "AUD"},
    "NZ": {"flag": "🇳🇿", "currency": "NZD"},
    "CA": {"flag": "🇨🇦", "currency": "CAD"},
    "CH": {"flag": "🇨🇭", "currency": "CHF"},
    "SE": {"flag": "🇸🇪", "currency": "SEK"},
    "NO": {"flag": "🇳🇴", "currency": "NOK"},
}

KEYWORD_COUNTRY = [
    ("PBoC", "CN"), ("Chinese", "CN"), ("China", "CN"), ("Caixin", "CN"),
    ("Fed's", "US"), ("Fed ", "US"), ("FOMC", "US"), ("US ", "US"), ("U.S.", "US"), ("Chicago", "US"), ("Richmond Fed", "US"), ("Redbook", "US"),
    ("ECB", "EU"), ("Eurozone", "EU"), ("Euro Area", "EU"),
    ("German", "DE"), ("Bund", "DE"), ("Bobl", "DE"), ("Schatz", "DE"), ("Ifo", "DE"), ("ZEW", "DE"), ("Bundesbank", "DE"),
    ("French", "FR"), ("Banque de France", "FR"),
    ("Italian", "IT"),
    ("Spanish", "ES"),
    ("Dutch", "NL"),
    ("UK ", "GB"), ("British", "GB"), ("BoE", "GB"),
    ("Japanese", "JP"), ("BoJ", "JP"), ("Tokyo", "JP"),
    ("Australian", "AU"), ("RBA", "AU"),
    ("New Zealand", "NZ"), ("RBNZ", "NZ"), ("Kiwi", "NZ"),
    ("Canadian", "CA"), ("BoC", "CA"), ("Canada", "CA"),
    ("Swiss", "CH"), ("SNB", "CH"),
    ("Swedish", "SE"), ("Riksbank", "SE"),
    ("Norwegian", "NO"),
]

HASH_ID_RE = re.compile(r"^[0-9a-f]{32}$")
SUFFIX_RE = re.compile(r"([A-Z]{2})$")

EXTRACT_JS = """
() => {
  const rows = Array.from(document.querySelectorAll('.div-table-row'));
  const events = [];
  let currentDate = null;
  for (const row of rows) {
    const dot = row.querySelector('.event-imp [class*="dot-"]');
    const titleEl = row.querySelector('.event-title');
    if (!dot || !titleEl) {
      const txt = row.textContent.trim();
      if (txt && !row.querySelector('.event-time') && txt.length < 30) {
        currentDate = txt;
      }
      continue;
    }
    const dotClass = Array.from(dot.classList).find(c => c.startsWith('dot-'));
    const impact = dotClass ? dotClass.split('-')[1] : null;
    const timeEl = row.querySelector('.event-time');
    const time = timeEl ? timeEl.textContent.trim() : '';
    const title = titleEl.textContent.trim();
    const dataRow = row.nextElementSibling;
    if (!dataRow) continue;
    const alertEl = dataRow.querySelector('[data-eventid]');
    if (!alertEl) continue;
    const id = alertEl.getAttribute('data-eventid');
    const actualEl = dataRow.querySelector('.event-actual');
    const forecastEl = dataRow.querySelector('.event-forcast');
    const previousEl = dataRow.querySelector('.event-previous');
    events.push({
      id,
      date: currentDate,
      time,
      title,
      impact,
      actual: actualEl ? actualEl.textContent.trim() : null,
      forecast: forecastEl ? forecastEl.textContent.trim() : null,
      previous: previousEl ? previousEl.textContent.trim() : null,
    });
  }
  return events;
}
"""

def apply_common_name(title):
    lower = title.lower()
    for phrase, acronym in ACRONYM_MAP.items():
        if phrase in lower and acronym not in title:
            return f"{title} ({acronym})"
    return title

def format_date_fr(d):
    return f"{FR_DAYS[d.weekday()]} {d.day} {FR_MONTHS[d.month - 1]}"

def detect_country(event_id, title):
    if not HASH_ID_RE.match(event_id):
        m = SUFFIX_RE.search(event_id)
        if m and m.group(1) in MARKETS:
            return m.group(1)
    for keyword, code in KEYWORD_COUNTRY:
        if keyword in title:
            return code
    return None

async def fetch_high_impact_events(target_date):
    target_label = target_date.strftime("%B") + " " + str(target_date.day)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(user_agent=UA, viewport={"width": 1366, "height": 900})
        await page.goto(URL, wait_until="domcontentloaded", timeout=45000)
        try:
            await page.wait_for_selector(".div-table-row .event-imp", timeout=25000)
        except Exception as exc:
            print(f"DEBUG: wait_for_selector failed: {exc}")
        await page.wait_for_timeout(4000)
        rows = await page.evaluate(EXTRACT_JS)
        await browser.close()

    print(f"DEBUG: target_label = {target_label!r}, total rows = {len(rows)}")
    target_rows = [r for r in rows if r["date"] == target_label]
    print(f"DEBUG: rows matching target date = {len(target_rows)}")
    red_rows = [r for r in target_rows if r["impact"] == "1"]
    print(f"DEBUG: red rows for target date = {[r['title'] for r in red_rows]}")
    return red_rows

def build_event_line(event, date_fr):
    country = detect_country(event["id"], event["title"])
    market_info = MARKETS.get(country)
    flag = market_info["flag"] if market_info else "🌍"
    currency = market_info["currency"] if market_info else "?"
    time_24h = event["time"] or "-"
    title = apply_common_name(event["title"])
    line = f"{flag} **{date_fr}** · **{time_24h}** · **{currency}** · {title}"

    details = []
    if event["forecast"] and event["forecast"] != "-":
        details.append(f"Prevision {event['forecast']}")
    if event["previous"] and event["previous"] != "-":
        details.append(f"Precedent {event['previous']}")
    if details:
        line += "\n> " + " · ".join(details)
    return line

def build_summary_embed(events, date_fr, title_suffix):
    lines = [build_event_line(event, date_fr) for event in events]
    description = "🕐 Fuseau horaire : Europe/Paris\n\n" + "\n\n".join(lines)
    return {
        "title": f"📅 ANNONCES ECONOMIQUES — {title_suffix}",
        "description": description[:4096],
        "color": RED_COLOR,
    }

def send_embed(embed):
    resp = requests.post(WEBHOOK_URL, json={"embeds": [embed]}, timeout=15)
    resp.raise_for_status()

def already_sent_today(date_str):
    if DEDUP_FILE.exists():
        return DEDUP_FILE.read_text(encoding="utf-8").strip() == date_str
    return False

def mark_sent(date_str):
    DEDUP_FILE.write_text(date_str, encoding="utf-8")

async def main():
    now_paris = datetime.now(PARIS)
    force = os.environ.get("FORCE_SEND") == "true"

    # Cible : 23h Paris. En cas de retard du declencheur GitHub, on rattrape
    # jusqu'a 4h du matin en utilisant toujours la date d'hier (le jour vise).
    if not force and now_paris.hour not in (23, 0, 1, 2, 3):
        print(f"Heure actuelle a Paris: {now_paris.isoformat()} — hors fenetre 23h-03h59, on ne fait rien.")
        return

    if force or now_paris.hour == 23:
        target_date = now_paris.date()
    else:
        target_date = (now_paris - timedelta(days=1)).date()

    target_str = target_date.isoformat()

    if not force and already_sent_today(target_str):
        print(f"Digest deja envoye pour {target_str}, on ne fait rien.")
        return

    date_fr = format_date_fr(target_date)

    events = await fetch_high_impact_events(target_date)
    # Ne garder que celles qui ne sont pas encore sorties (annonce a venir)
    events = [e for e in events if not (e["actual"] and e["actual"] != "-")]

    if not events:
        if force and os.environ.get("SEND_DEMO_IF_EMPTY") == "true":
            demo_event = {
                "id": "demoNFPUS",
                "time": "14:30",
                "title": "Non-Farm Payrolls",
                "actual": None,
                "forecast": "180K",
                "previous": "175K",
            }
            embed = build_summary_embed([demo_event], date_fr, "AUJOURD'HUI (EXEMPLE)")
            embed["description"] = (
                "Aucune annonce a fort impact a venir aujourd'hui — voici un exemple de rendu :\n\n"
                + embed["description"]
            )
            send_embed(embed)
            print("Aucune annonce a venir aujourd'hui — exemple de demonstration envoye.")
            return
        if not force:
            mark_sent(target_str)
        print("Aucune annonce a fort impact a venir, rien a envoyer.")
        return

    embed = build_summary_embed(events, date_fr, "AUJOURD'HUI")
    send_embed(embed)
    if not force:
        mark_sent(target_str)
    print(f"{len(events)} annonce(s) a fort impact a venir envoyee(s).")

if __name__ == "__main__":
    asyncio.run(main())
