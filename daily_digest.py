import asyncio
import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from playwright.async_api import async_playwright

URL = "https://www.forexfactory.com/calendar?day=today"
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

CURRENCY_INFO = {
    "USD": {"flag": "🇺🇸"},
    "EUR": {"flag": "🇪🇺"},
    "GBP": {"flag": "🇬🇧"},
    "JPY": {"flag": "🇯🇵"},
    "CNY": {"flag": "🇨🇳"},
    "AUD": {"flag": "🇦🇺"},
    "NZD": {"flag": "🇳🇿"},
    "CAD": {"flag": "🇨🇦"},
    "CHF": {"flag": "🇨🇭"},
    "SEK": {"flag": "🇸🇪"},
    "NOK": {"flag": "🇳🇴"},
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


def format_date_fr(dt):
    return f"{FR_DAYS[dt.weekday()]} {dt.day} {FR_MONTHS[dt.month - 1]}"


def format_time_24h(time_str):
    if not time_str:
        return "-"
    lowered = time_str.strip().lower()
    if lowered in ("all day", "tentative", "-"):
        return "Toute la journee" if lowered == "all day" else time_str
    match = re.match(r"(\d{1,2}):(\d{2})(am|pm)", lowered)
    if not match:
        return time_str
    hour, minute, meridiem = int(match.group(1)), match.group(2), match.group(3)
    if meridiem == "pm" and hour != 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    return f"{hour:02d}:{minute}"


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


def build_event_line(event, date_fr):
    flag = CURRENCY_INFO.get(event["currency"], {}).get("flag", "🌍")
    time_24h = format_time_24h(event["time"])
    title = apply_common_name(event["title"])
    line = f"{flag} **{date_fr}** · **{time_24h}** · **{event['currency']}** · {title}"

    details = []
    if event["actual"]:
        details.append(f"Actuel **{event['actual']}**")
    if event["forecast"]:
        details.append(f"Prevision {event['forecast']}")
    if event["previous"]:
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


def already_sent_today(today_str):
    if DEDUP_FILE.exists():
        return DEDUP_FILE.read_text(encoding="utf-8").strip() == today_str
    return False


def mark_sent(today_str):
    DEDUP_FILE.write_text(today_str, encoding="utf-8")


async def main():
    now_paris = datetime.now(PARIS)
    force = os.environ.get("FORCE_SEND") == "true"
    today_str = now_paris.strftime("%Y-%m-%d")

    if not force and now_paris.hour != 23:
        print(f"Heure actuelle a Paris: {now_paris.isoformat()} — pas encore 23h, on ne fait rien.")
        return

    if not force and already_sent_today(today_str):
        print(f"Digest deja envoye aujourd'hui ({today_str}), on ne fait rien.")
        return

    date_fr = format_date_fr(now_paris)

    events = await fetch_today_high_impact_events()

    if not events:
        if force and os.environ.get("SEND_DEMO_IF_EMPTY") == "true":
            demo_event = {
                "time": "2:30pm",
                "currency": "USD",
                "title": "Non-Farm Payrolls",
                "actual": "210K",
                "forecast": "180K",
                "previous": "175K",
            }
            embed = build_summary_embed([demo_event], date_fr, "AUJOURD'HUI (EXEMPLE)")
            embed["description"] = (
                "Aucune annonce a fort impact reelle aujourd'hui — voici un exemple de rendu :\n\n"
                + embed["description"]
            )
            send_embed(embed)
            print("Aucune annonce reelle aujourd'hui — exemple de demonstration envoye.")
            return
        if not force:
            mark_sent(today_str)
        print("Aucune annonce a fort impact aujourd'hui, rien a envoyer.")
        return

    embed = build_summary_embed(events, date_fr, "AUJOURD'HUI")
    send_embed(embed)
    if not force:
        mark_sent(today_str)
    print(f"{len(events)} annonce(s) a fort impact envoyee(s).")


if __name__ == "__main__":
    asyncio.run(main())
