import asyncio
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from playwright.async_api import async_playwright

URL = "https://www.forexfactory.com/calendar?day=today"
WEBHOOK_URL = os.environ["FOREXFACTORY_WEBHOOK_URL"]
PARIS = ZoneInfo("Europe/Paris")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

RED_COLOR = 0xE0201B

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

CURRENCY_INFO = {
    "USD": {"flag": "🇺🇸", "pairs": ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "USD/CAD", "AUD/USD"], "indices": ["S&P 500", "Nasdaq 100", "Dow Jones"], "other": ["Or (XAU/USD)", "Petrole WTI"]},
    "EUR": {"flag": "🇪🇺", "pairs": ["EUR/USD", "EUR/GBP", "EUR/JPY"], "indices": ["DAX", "CAC 40", "Euro Stoxx 50"], "other": []},
    "GBP": {"flag": "🇬🇧", "pairs": ["GBP/USD", "EUR/GBP", "GBP/JPY"], "indices": ["FTSE 100"], "other": []},
    "JPY": {"flag": "🇯🇵", "pairs": ["USD/JPY", "EUR/JPY", "GBP/JPY"], "indices": ["Nikkei 225"], "other": []},
    "CNY": {"flag": "🇨🇳", "pairs": ["USD/CNH", "AUD/USD"], "indices": ["Shanghai Composite", "Hang Seng", "ASX 200"], "other": ["Cuivre", "Minerai de fer"]},
    "AUD": {"flag": "🇦🇺", "pairs": ["AUD/USD", "AUD/JPY", "AUD/NZD"], "indices": ["ASX 200"], "other": []},
    "NZD": {"flag": "🇳🇿", "pairs": ["NZD/USD", "AUD/NZD"], "indices": ["NZX 50"], "other": []},
    "CAD": {"flag": "🇨🇦", "pairs": ["USD/CAD", "CAD/JPY"], "indices": ["TSX"], "other": ["Petrole WTI"]},
    "CHF": {"flag": "🇨🇭", "pairs": ["USD/CHF", "EUR/CHF"], "indices": ["SMI"], "other": []},
    "SEK": {"flag": "🇸🇪", "pairs": ["USD/SEK", "EUR/SEK"], "indices": ["OMXS30"], "other": []},
    "NOK": {"flag": "🇳🇴", "pairs": ["USD/NOK", "EUR/NOK"], "indices": ["OBX"], "other": ["Petrole Brent"]},
}

EXTRACT_JS = """
() => {
  const rows = Array.from(document.querySelectorAll('tr.calendar__row[data-event-id]'));
  return rows.map(row => {
    const icon = row.querySelector('.calendar__impact .icon');
    const impactClass = icon ? icon.className : '';
    const timeEl = row.querySelector('.calendar__time');
    const currencyEl = row.querySelector('.calendar__currency span');
    const titleEl = row.querySelector('.calendar__event-title');
    const actualEl = row.querySelector('.calendar__actual span');
    const forecastEl = row.querySelector('.calendar__forecast span');
    const previousEl = row.querySelector('.calendar__previous span');
    return {
      impactClass,
      time: timeEl ? timeEl.textContent.trim() : '-',
      currency: currencyEl ? currencyEl.textContent.trim() : '?',
      title: titleEl ? titleEl.textContent.trim() : '?',
      actual: actualEl ? actualEl.textContent.trim() : null,
      forecast: forecastEl ? forecastEl.textContent.trim() : null,
      previous: previousEl ? previousEl.textContent.trim() : null,
    };
  });
}
"""


def apply_common_name(title):
    lower = title.lower()
    for phrase, acronym in ACRONYM_MAP.items():
        if phrase in lower and acronym not in title:
            return f"{title} ({acronym})"
    return title


async def fetch_today_high_impact_events():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(user_agent=UA, viewport={"width": 1366, "height": 900})
        await page.goto(URL, wait_until="domcontentloaded", timeout=45000)
        try:
            await page.wait_for_selector("tr.calendar__row[data-event-id]", timeout=20000)
        except Exception:
            pass
        rows = await page.evaluate(EXTRACT_JS)
        await browser.close()

    return [row for row in rows if "icon--ff-impact-red" in row["impactClass"]]


def build_embed(event, today_label):
    currency_info = CURRENCY_INFO.get(event["currency"])
    flag = currency_info["flag"] if currency_info else "🌍"
    display_title = apply_common_name(event["title"])

    embed = {
        "title": f"🔴 {flag} {display_title}",
        "description": f"**Impact eleve** · Devise **{event['currency']}** · {today_label} a {event['time']}",
        "color": RED_COLOR,
        "fields": [
            {"name": "📌 Actuel", "value": event["actual"] or "-", "inline": True},
            {"name": "🎯 Prevision", "value": event["forecast"] or "-", "inline": True},
            {"name": "📜 Precedent", "value": event["previous"] or "-", "inline": True},
        ],
    }

    if currency_info:
        lines = []
        if currency_info["pairs"]:
            lines.append("**Paires Forex :** " + ", ".join(currency_info["pairs"]))
        if currency_info["indices"]:
            lines.append("**Indices :** " + ", ".join(currency_info["indices"]))
        if currency_info["other"]:
            lines.append("**Autres :** " + ", ".join(currency_info["other"]))
        embed["fields"].append({"name": "💱 Marches probablement concernes", "value": "\n".join(lines), "inline": False})

    return embed


def send_digest(events, today_label):
    header = {
        "content": f"📅 **Recapitulatif des annonces economiques a fort impact — {today_label}** ({len(events)} annonce{'s' if len(events) > 1 else ''})",
        "embeds": [build_embed(event, today_label) for event in events[:10]],
    }
    resp = requests.post(WEBHOOK_URL, json=header, timeout=15)
    resp.raise_for_status()

    remaining = events[10:]
    while remaining:
        batch, remaining = remaining[:10], remaining[10:]
        resp = requests.post(WEBHOOK_URL, json={"embeds": [build_embed(e, today_label) for e in batch]}, timeout=15)
        resp.raise_for_status()


async def main():
    now_paris = datetime.now(PARIS)
    force = os.environ.get("FORCE_SEND") == "true"
    if not force and now_paris.hour != 23:
        print(f"Heure actuelle a Paris: {now_paris.isoformat()} — pas encore 23h, on ne fait rien.")
        return

    today_label = now_paris.strftime("%A %d %B %Y")
    events = await fetch_today_high_impact_events()

    if not events:
        print("Aucune annonce a fort impact aujourd'hui, rien a envoyer.")
        return

    send_digest(events, today_label)
    print(f"{len(events)} annonce(s) a fort impact envoyee(s).")


if __name__ == "__main__":
    asyncio.run(main())
