import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from playwright.async_api import async_playwright

URL = "https://www.financialjuice.com/"
STATE_FILE = Path("state.json")
WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]

# imp-1 = rouge (impact eleve), imp-2 = orange (impact moyen)
WATCHED_IMPACTS = {"1", "2"}
IMPACT_LABELS = {
    "1": {"icon": "🔴", "label": "Impact eleve", "color": 0xE0201B},
    "2": {"icon": "🟠", "label": "Impact moyen", "color": 0xF0A500},
}

MARKETS = {
    "US": {"flag": "🇺🇸", "currency": "USD", "pairs": ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "USD/CAD", "AUD/USD"], "indices": ["S&P 500", "Nasdaq 100", "Dow Jones"], "other": ["Or (XAU/USD)", "Petrole WTI"]},
    "EU": {"flag": "🇪🇺", "currency": "EUR", "pairs": ["EUR/USD", "EUR/GBP", "EUR/JPY"], "indices": ["DAX", "CAC 40", "Euro Stoxx 50"], "other": []},
    "DE": {"flag": "🇩🇪", "currency": "EUR", "pairs": ["EUR/USD", "EUR/GBP"], "indices": ["DAX", "Euro Stoxx 50"], "other": []},
    "FR": {"flag": "🇫🇷", "currency": "EUR", "pairs": ["EUR/USD", "EUR/GBP"], "indices": ["CAC 40", "Euro Stoxx 50"], "other": []},
    "IT": {"flag": "🇮🇹", "currency": "EUR", "pairs": ["EUR/USD"], "indices": ["FTSE MIB", "Euro Stoxx 50"], "other": []},
    "ES": {"flag": "🇪🇸", "currency": "EUR", "pairs": ["EUR/USD"], "indices": ["IBEX 35", "Euro Stoxx 50"], "other": []},
    "NL": {"flag": "🇳🇱", "currency": "EUR", "pairs": ["EUR/USD"], "indices": ["AEX", "Euro Stoxx 50"], "other": []},
    "GB": {"flag": "🇬🇧", "currency": "GBP", "pairs": ["GBP/USD", "EUR/GBP", "GBP/JPY"], "indices": ["FTSE 100"], "other": []},
    "UK": {"flag": "🇬🇧", "currency": "GBP", "pairs": ["GBP/USD", "EUR/GBP", "GBP/JPY"], "indices": ["FTSE 100"], "other": []},
    "JP": {"flag": "🇯🇵", "currency": "JPY", "pairs": ["USD/JPY", "EUR/JPY", "GBP/JPY"], "indices": ["Nikkei 225"], "other": []},
    "CN": {"flag": "🇨🇳", "currency": "CNY", "pairs": ["USD/CNH", "AUD/USD"], "indices": ["Shanghai Composite", "Hang Seng", "ASX 200"], "other": ["Cuivre", "Minerai de fer"]},
    "AU": {"flag": "🇦🇺", "currency": "AUD", "pairs": ["AUD/USD", "AUD/JPY", "AUD/NZD"], "indices": ["ASX 200"], "other": []},
    "NZ": {"flag": "🇳🇿", "currency": "NZD", "pairs": ["NZD/USD", "AUD/NZD"], "indices": ["NZX 50"], "other": []},
    "CA": {"flag": "🇨🇦", "currency": "CAD", "pairs": ["USD/CAD", "CAD/JPY"], "indices": ["TSX"], "other": ["Petrole WTI"]},
    "CH": {"flag": "🇨🇭", "currency": "CHF", "pairs": ["USD/CHF", "EUR/CHF"], "indices": ["SMI"], "other": []},
    "SE": {"flag": "🇸🇪", "currency": "SEK", "pairs": ["USD/SEK", "EUR/SEK"], "indices": ["OMXS30"], "other": []},
    "NO": {"flag": "🇳🇴", "currency": "NOK", "pairs": ["USD/NOK", "EUR/NOK"], "indices": ["OBX"], "other": ["Petrole Brent"]},
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


async def scrape():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1366, "height": 900},
        )
        await page.goto(URL, wait_until="domcontentloaded", timeout=45000)
        try:
            await page.wait_for_selector(".div-table-row .event-imp", timeout=25000)
        except Exception:
            pass
        await page.wait_for_timeout(4000)
        events = await page.evaluate(EXTRACT_JS)
        if not events:
            try:
                await page.screenshot(path="debug.png", full_page=True)
                Path("debug.html").write_text(await page.content(), encoding="utf-8")
            except Exception as exc:
                print(f"Impossible de sauvegarder le debug: {exc}", file=sys.stderr)
        await browser.close()
        return events


def detect_country(event_id, title):
    if not HASH_ID_RE.match(event_id):
        m = SUFFIX_RE.search(event_id)
        if m and m.group(1) in MARKETS:
            return m.group(1)
    for keyword, code in KEYWORD_COUNTRY:
        if keyword in title:
            return code
    return None


def parse_num(value):
    if not value or value == "-":
        return None
    cleaned = value.replace(",", "").replace("%", "")
    match = re.match(r"^(-?[\d.]+)", cleaned)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def compare_actual(actual, forecast):
    a, f = parse_num(actual), parse_num(forecast)
    if a is None or f is None:
        return "➡️", "Non comparable a la prevision"
    if a > f:
        return "🔼", "Superieur a la prevision"
    if a < f:
        return "🔽", "Inferieur a la prevision"
    return "➡️", "Conforme a la prevision"


def build_embed(event, country):
    impact_info = IMPACT_LABELS[event["impact"]]
    market_info = MARKETS.get(country)
    flag = market_info["flag"] if market_info else "🌍"
    currency = market_info["currency"] if market_info else "?"
    cmp_icon, cmp_label = compare_actual(event["actual"], event["forecast"])

    fields = [
        {"name": "📌 Actuel", "value": event["actual"] or "-", "inline": True},
        {"name": "🎯 Prevision", "value": event["forecast"] or "-", "inline": True},
        {"name": "📜 Precedent", "value": event["previous"] or "-", "inline": True},
        {"name": "📊 Resultat", "value": f"{cmp_icon} {cmp_label}", "inline": False},
    ]

    if market_info:
        lines = []
        if market_info["pairs"]:
            lines.append("**Paires Forex :** " + ", ".join(market_info["pairs"]))
        if market_info["indices"]:
            lines.append("**Indices :** " + ", ".join(market_info["indices"]))
        if market_info["other"]:
            lines.append("**Autres :** " + ", ".join(market_info["other"]))
        fields.append({"name": "💱 Marches probablement concernes", "value": "\n".join(lines), "inline": False})

    return {
        "title": f"{impact_info['icon']} {flag} {event['title']}",
        "description": f"**{impact_info['label']}** · Devise **{currency}** · {event['date']} a {event['time']}",
        "color": impact_info["color"],
        "fields": fields,
        "footer": {"text": "Source : FinancialJuice"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def send_discord(embed):
    payload = {"username": "FinancialJuice Alerts", "embeds": [embed]}
    resp = requests.post(WEBHOOK_URL, json=payload, timeout=15)
    resp.raise_for_status()


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def save_state(state):
    if len(state) > 3000:
        state = dict(list(state.items())[-1500:])
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


async def main():
    events = await scrape()
    state = load_state()
    sent = 0

    for event in events:
        if event["impact"] not in WATCHED_IMPACTS:
            continue
        if not event["actual"] or event["actual"] == "-":
            continue

        key = f"{event['date']}_{event['id']}"
        if state.get(key) == event["actual"]:
            continue

        country = detect_country(event["id"], event["title"])
        embed = build_embed(event, country)
        try:
            send_discord(embed)
            state[key] = event["actual"]
            sent += 1
        except Exception as exc:
            print(f"Erreur envoi Discord pour '{event['title']}': {exc}", file=sys.stderr)

    save_state(state)
    print(f"{sent} nouvelle(s) news envoyee(s) sur {len(events)} evenements scannes.")


if __name__ == "__main__":
    asyncio.run(main())
