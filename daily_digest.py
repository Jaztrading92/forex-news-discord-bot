"""Annonces economiques du lendemain (calendrier ForexFactory) publiees sur Discord a 23h (Paris).

Pas de navigateur : le calendrier est un simple fichier JSON, on le telecharge avec requests.
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

CALENDAR_URLS = [
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
]
WEBHOOK_URL = os.environ.get("FOREXFACTORY_WEBHOOK_URL", "")
PARIS = ZoneInfo("Europe/Paris")
DEDUP_FILE = Path("digest_last_sent.txt")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

COLOR = 0xE0201B
IMPACT_ICON = {"High": "🔴", "Medium": "🟠"}

FR_DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
FR_MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
             "septembre", "octobre", "novembre", "décembre"]

CURRENCY_FLAG = {
    "USD": "🇺🇸", "EUR": "🇪🇺", "GBP": "🇬🇧", "JPY": "🇯🇵", "CAD": "🇨🇦", "AUD": "🇦🇺",
    "NZD": "🇳🇿", "CHF": "🇨🇭", "CNY": "🇨🇳", "All": "🌍",
}

COUNTRY_PREFIX = {
    "German": "Allemagne", "French": "France", "Spanish": "Espagne", "Italian": "Italie",
    "Chinese": "Chine", "Japanese": "Japon",
}
STAGE = {"Flash": "flash", "Prelim": "préliminaire", "Final": "final", "Revised": "révisé", "Advance": "1re estimation"}

# Du plus precis au plus general : la premiere regle qui correspond s'applique a ce morceau du titre.
GLOSSARY = [
    ("ADP Non-Farm Employment Change", "Emplois privés ADP"),
    ("Non-Farm Employment Change", "Créations d'emplois non agricoles (NFP)"),
    ("Unemployment Claims", "Inscriptions hebdomadaires au chômage"),
    ("Unemployment Rate", "Taux de chômage"),
    ("Unemployment Change", "Variation du chômage"),
    ("Claimant Count Change", "Variation des demandeurs d'emploi"),
    ("Employment Cost Index", "Indice du coût de l'emploi"),
    ("Employment Change", "Variation de l'emploi"),
    ("Average Hourly Earnings", "Salaire horaire moyen"),
    ("Average Earnings Index", "Indice des salaires"),
    ("Wage Price Index", "Indice des salaires"),
    ("JOLTS Job Openings", "Offres d'emploi JOLTS"),
    ("Core PCE Price Index", "Inflation PCE de base"),
    ("PCE Price Index", "Inflation PCE"),
    ("Core CPI", "Inflation de base (IPC)"),
    ("Median CPI", "Inflation médiane (IPC)"),
    ("Trimmed CPI", "Inflation tronquée (IPC)"),
    ("Common CPI", "Inflation commune (IPC)"),
    ("CPI", "Inflation (IPC)"),
    ("Core PPI", "Prix à la production de base (IPP)"),
    ("PPI", "Prix à la production (IPP)"),
    ("Core Retail Sales", "Ventes au détail de base"),
    ("Retail Sales", "Ventes au détail"),
    ("GDP Price Index", "Indice des prix du PIB"),
    ("GDP", "PIB (croissance)"),
    ("ISM Manufacturing PMI", "ISM manufacturier"),
    ("ISM Services PMI", "ISM des services"),
    ("Manufacturing PMI", "PMI manufacturier"),
    ("Services PMI", "PMI des services"),
    ("Composite PMI", "PMI composite"),
    ("Federal Funds Rate", "Décision de taux de la Fed"),
    ("FOMC Statement", "Communiqué de la Fed (FOMC)"),
    ("FOMC Press Conference", "Conférence de presse de la Fed (FOMC)"),
    ("FOMC Meeting Minutes", "Compte rendu de la Fed (FOMC)"),
    ("Main Refinancing Rate", "Décision de taux de la BCE"),
    ("ECB Press Conference", "Conférence de presse de la BCE"),
    ("Monetary Policy Statement", "Communiqué de politique monétaire"),
    ("Monetary Policy Summary", "Résumé de politique monétaire"),
    ("MPC Official Bank Rate Votes", "Votes du comité de la BoE"),
    ("Official Bank Rate", "Décision de taux de la BoE"),
    ("Official Cash Rate", "Décision de taux de la RBNZ"),
    ("Cash Rate", "Décision de taux de la RBA"),
    ("Overnight Rate", "Décision de taux de la BoC"),
    ("BOJ Policy Rate", "Décision de taux de la BoJ"),
    ("SNB Policy Rate", "Décision de taux de la BNS"),
    ("Interest Rate Decision", "Décision de taux"),
    ("Press Conference", "Conférence de presse"),
    ("Trade Balance", "Balance commerciale"),
    ("Current Account", "Balance courante"),
    ("CB Consumer Confidence", "Confiance des consommateurs (Conference Board)"),
    ("Consumer Confidence", "Confiance des consommateurs"),
    ("UoM Consumer Sentiment", "Moral des ménages (Univ. Michigan)"),
    ("UoM Inflation Expectations", "Anticipations d'inflation (Univ. Michigan)"),
    ("Core Durable Goods Orders", "Commandes de biens durables de base"),
    ("Durable Goods Orders", "Commandes de biens durables"),
    ("Industrial Production", "Production industrielle"),
    ("Building Permits", "Permis de construire"),
    ("Housing Starts", "Mises en chantier"),
    ("Existing Home Sales", "Ventes de logements anciens"),
    ("New Home Sales", "Ventes de logements neufs"),
    ("Pending Home Sales", "Promesses de ventes de logements"),
    ("Crude Oil Inventories", "Stocks de pétrole brut"),
    ("Natural Gas Storage", "Stocks de gaz naturel"),
    ("Empire State Manufacturing Index", "Indice manufacturier Empire State"),
    ("Philly Fed Manufacturing Index", "Indice manufacturier de la Fed de Philadelphie"),
    ("Ifo Business Climate", "Climat des affaires Ifo"),
    ("ZEW Economic Sentiment", "Sentiment économique ZEW"),
    ("Tankan Manufacturing Index", "Indice Tankan manufacturier"),
    ("Beige Book", "Livre beige de la Fed"),
    ("Bank Holiday", "Jour férié"),
    ("Fed Chair", "président de la Fed"),
    ("ECB President", "présidente de la BCE"),
    ("BOE Gov", "gouverneur de la BoE"),
    ("BOJ Gov", "gouverneur de la BoJ"),
    ("BOC Gov", "gouverneur de la BoC"),
    ("RBA Gov", "gouverneure de la RBA"),
    ("RBNZ Gov", "gouverneur de la RBNZ"),
    ("SNB Chairman", "président de la BNS"),
    ("FOMC Member", "membre de la Fed"),
    ("MPC Member", "membre de la BoE"),
]
GLOSSARY_RE = [(re.compile(r"\b" + re.escape(en) + r"\b"), fr) for en, fr in GLOSSARY]
PERIOD_SUFFIX = {"m/m": "sur 1 mois", "q/q": "sur 1 trimestre", "y/y": "sur 1 an", "3m/y": "sur 3 mois / 1 an"}


def translate_online(text):
    try:
        resp = requests.get(
            "https://api.mymemory.translated.net/get",
            params={"q": text, "langpair": "en|fr"},
            timeout=5,
        )
        translated = resp.json()["responseData"]["translatedText"]
        if translated and "MYMEMORY" not in translated.upper():
            return translated
    except Exception as exc:
        print(f"Traduction en ligne impossible pour {text!r}: {exc}", file=sys.stderr)
    return text


def translate_title(title):
    core = title.strip()
    country = ""
    first, _, rest = core.partition(" ")
    if first in COUNTRY_PREFIX and rest:
        country, core = COUNTRY_PREFIX[first] + " – ", rest

    stage = ""
    first, _, rest = core.partition(" ")
    if first in STAGE and rest:
        stage, core = f" ({STAGE[first]})", rest

    period = ""
    for en, fr in PERIOD_SUFFIX.items():
        if core.endswith(" " + en):
            period, core = f" {fr}", core[: -len(en) - 1]
            break

    speech = core.endswith(" Speaks")
    if speech:
        core = core[: -len(" Speaks")]

    matched = False
    for pattern, fr in GLOSSARY_RE:
        if pattern.search(core):
            core = pattern.sub(fr, core, count=1)
            matched = True
            break
    if not matched and not speech:
        core = translate_online(core)

    if speech:
        core = f"Discours : {core}"
    return f"{country}{core}{period}{stage}"


def format_date_fr(day):
    return f"{FR_DAYS[day.weekday()]} {day.day} {FR_MONTHS[day.month - 1]}"


def fetch_json(url):
    last_exc = None
    for attempt in range(1, 4):
        try:
            resp = requests.get(url, headers={"User-Agent": UA}, timeout=20)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            last_exc = exc
            print(f"Echec telechargement calendrier (essai {attempt}/3) {url}: {exc}", file=sys.stderr)
            time.sleep(15 * attempt)
    raise RuntimeError(f"calendrier inaccessible : {last_exc}")


def fetch_events(target_date):
    """Annonces d'impact moyen/fort du jour `target_date` (heure de Paris), triees par heure."""
    events = []
    for url in CALENDAR_URLS:
        data = fetch_json(url)
        if not data:
            continue
        for item in data:
            when = datetime.fromisoformat(item["date"]).astimezone(PARIS)
            if when.date() == target_date and item.get("impact") in IMPACT_ICON:
                events.append({**item, "when": when})
        # La semaine en cours suffit si elle couvre deja le jour vise.
        last_day = max(datetime.fromisoformat(i["date"]).astimezone(PARIS).date() for i in data)
        if last_day >= target_date:
            break
    events.sort(key=lambda e: (e["when"], e["impact"] != "High"))
    return events


def build_lines(events):
    lines = []
    for e in events:
        flag = CURRENCY_FLAG.get(e["country"], "🌍")
        line = f"{IMPACT_ICON[e['impact']]} **{e['when']:%H:%M}** {flag} **{e['country']}** — {translate_title(e['title'])}"
        details = []
        if e.get("forecast"):
            details.append(f"Prévision : **{e['forecast']}**")
        if e.get("previous"):
            details.append(f"Précédent : {e['previous']}")
        if details:
            line += "\n> " + " · ".join(details)
        lines.append(line)
    return lines


def build_embeds(events, target_date):
    header = f"🗓️ **{format_date_fr(target_date).capitalize()}** · heures de Paris\n\n"
    chunks, current = [], header
    for line in build_lines(events):
        if len(current) + len(line) + 2 > 3900:
            chunks.append(current)
            current = ""
        current += line + "\n\n"
    chunks.append(current)

    embeds = []
    for i, text in enumerate(chunks):
        embed = {"description": text.strip(), "color": COLOR}
        if i == 0:
            embed["title"] = "📅 ANNONCES ÉCONOMIQUES DE DEMAIN"
        if i == len(chunks) - 1:
            embed["footer"] = {"text": "🔴 Fort impact · 🟠 Impact moyen · Restez prudents autour des annonces"}
        embeds.append(embed)
    return embeds


def send_embeds(embeds):
    for start in range(0, len(embeds), 10):
        payload = {"embeds": embeds[start:start + 10]}
        for attempt in range(1, 4):
            resp = requests.post(WEBHOOK_URL, json=payload, timeout=15)
            if resp.status_code == 404:
                raise RuntimeError("webhook Discord introuvable (supprime ?) : il faut en creer un nouveau.")
            if resp.status_code == 429 or resp.status_code >= 500:
                wait = float(resp.headers.get("Retry-After", 5 * attempt))
                print(f"Discord occupe ({resp.status_code}), nouvel essai dans {wait}s.", file=sys.stderr)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            break
        else:
            raise RuntimeError("Discord refuse l'envoi apres 3 essais.")


def already_sent(key):
    return DEDUP_FILE.exists() and DEDUP_FILE.read_text(encoding="utf-8").strip() == key


def mark_sent(key):
    DEDUP_FILE.write_text(key, encoding="utf-8")


def main():
    now = datetime.now(PARIS)
    force = os.environ.get("FORCE_SEND") == "true"

    if not force and now.hour not in (23, 0, 1, 2, 3):
        print(f"{now:%H:%M} a Paris : envoi prevu a 23h, rien a faire.")
        return

    # A 23h on annonce le lendemain ; entre 0h et 3h (rattrapage si GitHub a pris du retard)
    # ce lendemain est deja "aujourd'hui".
    target_date = now.date() + timedelta(days=1) if (force or now.hour == 23) else now.date()
    key = target_date.isoformat()

    if not force and already_sent(key):
        print(f"Annonces du {key} deja envoyees.")
        return

    events = fetch_events(target_date)
    print(f"{len(events)} annonce(s) d'impact moyen/fort le {key}.")

    # Comme avant : on ne publie que s'il y a au moins une annonce a fort impact.
    if not any(e["impact"] == "High" for e in events):
        print("Aucune annonce a fort impact, rien a publier.")
        if not force:
            mark_sent(key)
        return

    embeds = build_embeds(events, target_date)
    if os.environ.get("DRY_RUN") == "true":
        print("Mode test, rien n'est envoye. Message prevu :")
        print(json.dumps(embeds, ensure_ascii=False, indent=2))
        return

    send_embeds(embeds)
    if not force:
        mark_sent(key)
    print(f"Annonces du {key} publiees.")


if __name__ == "__main__":
    main()
