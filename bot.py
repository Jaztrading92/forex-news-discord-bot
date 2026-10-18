import asyncio
import json
import os
import re
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import discord
import requests
from playwright.async_api import async_playwright

URL = "https://www.financialjuice.com/"
STATE_FILE = Path("state.json")
BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
CHANNEL_ID = int(os.environ["DISCORD_CHANNEL_ID"])
ALERT_CHANNEL_ID = int(os.environ.get("DISCORD_ALERT_CHANNEL_ID") or 0) or CHANNEL_ID

POLL_INTERVAL_SECONDS = 20
UNHEALTHY_ALERT_SECONDS = 15 * 60
BROWSER_LAUNCH_ATTEMPTS = 5
RELOAD_EVERY_N_TICKS = 15  # ~5 min entre deux rechargements complets de la page (evite un flux temps reel fige)
MAX_RUNTIME_SECONDS = 5 * 3600 + 30 * 60  # 5h30 puis arret propre pour relance par le workflow

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

RED_COLOR = 0xB80101
GREY_COLOR = 0x6C7A89

MARKETS = {
    "US": {"flag": "🇺🇸", "pairs": ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "USD/CAD", "AUD/USD"], "indices": ["S&P 500", "Nasdaq 100", "Dow Jones"], "other": ["Or (XAU/USD)", "Petrole WTI"]},
    "EU": {"flag": "🇪🇺", "pairs": ["EUR/USD", "EUR/GBP", "EUR/JPY"], "indices": ["DAX", "CAC 40", "Euro Stoxx 50"], "other": []},
    "DE": {"flag": "🇩🇪", "pairs": ["EUR/USD", "EUR/GBP"], "indices": ["DAX", "Euro Stoxx 50"], "other": []},
    "FR": {"flag": "🇫🇷", "pairs": ["EUR/USD", "EUR/GBP"], "indices": ["CAC 40", "Euro Stoxx 50"], "other": []},
    "IT": {"flag": "🇮🇹", "pairs": ["EUR/USD"], "indices": ["FTSE MIB", "Euro Stoxx 50"], "other": []},
    "ES": {"flag": "🇪🇸", "pairs": ["EUR/USD"], "indices": ["IBEX 35", "Euro Stoxx 50"], "other": []},
    "NL": {"flag": "🇳🇱", "pairs": ["EUR/USD"], "indices": ["AEX", "Euro Stoxx 50"], "other": []},
    "GB": {"flag": "🇬🇧", "pairs": ["GBP/USD", "EUR/GBP", "GBP/JPY"], "indices": ["FTSE 100"], "other": []},
    "JP": {"flag": "🇯🇵", "pairs": ["USD/JPY", "EUR/JPY", "GBP/JPY"], "indices": ["Nikkei 225"], "other": []},
    "CN": {"flag": "🇨🇳", "pairs": ["USD/CNH", "AUD/USD"], "indices": ["Shanghai Composite", "Hang Seng", "ASX 200"], "other": ["Cuivre", "Minerai de fer"]},
    "AU": {"flag": "🇦🇺", "pairs": ["AUD/USD", "AUD/JPY", "AUD/NZD"], "indices": ["ASX 200"], "other": []},
    "NZ": {"flag": "🇳🇿", "pairs": ["NZD/USD", "AUD/NZD"], "indices": ["NZX 50"], "other": []},
    "CA": {"flag": "🇨🇦", "pairs": ["USD/CAD", "CAD/JPY"], "indices": ["TSX"], "other": ["Petrole WTI"]},
    "CH": {"flag": "🇨🇭", "pairs": ["USD/CHF", "EUR/CHF"], "indices": ["SMI"], "other": []},
    "SE": {"flag": "🇸🇪", "pairs": ["USD/SEK", "EUR/SEK"], "indices": ["OMXS30"], "other": []},
    "NO": {"flag": "🇳🇴", "pairs": ["USD/NOK", "EUR/NOK"], "indices": ["OBX"], "other": ["Petrole Brent"]},
    "RU": {"flag": "🇷🇺", "pairs": ["USD/RUB"], "indices": [], "other": ["Petrole Brent"]},
    "UA": {"flag": "🇺🇦", "pairs": [], "indices": [], "other": []},
    "IN": {"flag": "🇮🇳", "pairs": ["USD/INR"], "indices": ["Nifty 50", "Sensex"], "other": []},
    "BR": {"flag": "🇧🇷", "pairs": ["USD/BRL"], "indices": ["Bovespa"], "other": []},
    "MX": {"flag": "🇲🇽", "pairs": ["USD/MXN"], "indices": [], "other": []},
    "TR": {"flag": "🇹🇷", "pairs": ["USD/TRY"], "indices": [], "other": []},
    "SA": {"flag": "🇸🇦", "pairs": [], "indices": [], "other": ["Petrole Brent"]},
    "OIL": {"flag": "🛢️", "pairs": ["USD/CAD", "USD/NOK"], "indices": [], "other": ["Petrole Brent", "Petrole WTI"]},
    "GOLD": {"flag": "🥇", "pairs": [], "indices": [], "other": ["Or (XAU/USD)"]},
}

# Le site ne fournit plus toujours d'etiquettes pays : on deduit aussi le pays du titre.
TITLE_KEYWORDS = [(re.compile(pattern), code) for pattern, code in [
    (r"\bFed\b|\bFOMC\b|\bPowell\b|\bTreasury\b|\bUS\b|\bU\.S\.|\bTrump\b|\bWhite House\b|\bNonfarm\b", "US"),
    (r"\bECB\b|\bLagarde\b|\bEurozone\b|\bEuro Area\b|\bEU\b", "EU"),
    (r"\bGerman|\bBundesbank\b", "DE"),
    (r"\bFrench\b|\bFrance\b", "FR"),
    (r"\bItaly\b|\bItalian\b", "IT"),
    (r"\bSpain\b|\bSpanish\b", "ES"),
    (r"\bUK\b|\bBritish\b|\bBritain\b|\bBoE\b|\bBailey\b", "GB"),
    (r"\bJapan|\bBoJ\b|\bUeda\b", "JP"),
    (r"\bChina\b|\bChinese\b|\bPBoC\b|\bBeijing\b", "CN"),
    (r"\bAustralia|\bRBA\b", "AU"),
    (r"\bNew Zealand\b|\bRBNZ\b", "NZ"),
    (r"\bCanada\b|\bCanadian\b|\bBoC\b", "CA"),
    (r"\bSwiss\b|\bSwitzerland\b|\bSNB\b", "CH"),
    (r"\bRussia", "RU"),
    (r"\bUkrain", "UA"),
    (r"\bIndia\b|\bIndian\b", "IN"),
    (r"\bSaudi\b", "SA"),
    (r"\bTurkey\b|\bTurkish\b", "TR"),
    (r"\bOPEC\b|\b[Oo]il\b|\bBrent\b|\bWTI\b|\b[Cc]rude\b|\brefinery\b|\bHormuz\b", "OIL"),
    (r"\b[Gg]old\b", "GOLD"),
]]

LABEL_TO_COUNTRY = {
    "USD": "US", "US": "US", "United States": "US", "America": "US", "U.S.": "US",
    "EUR": "EU", "Europe": "EU", "Eurozone": "EU", "European Union": "EU",
    "GBP": "GB", "UK": "GB", "United Kingdom": "GB", "Britain": "GB",
    "JPY": "JP", "Japan": "JP", "Japanese": "JP",
    "CNY": "CN", "China": "CN", "Chinese": "CN",
    "AUD": "AU", "Australia": "AU", "Australian": "AU",
    "NZD": "NZ", "New Zealand": "NZ",
    "CAD": "CA", "Canada": "CA", "Canadian": "CA",
    "CHF": "CH", "Switzerland": "CH", "Swiss": "CH",
    "SEK": "SE", "Sweden": "SE",
    "NOK": "NO", "Norway": "NO",
    "Germany": "DE", "German": "DE",
    "France": "FR", "French": "FR",
    "Italy": "IT", "Italian": "IT",
    "Spain": "ES", "Spanish": "ES",
    "Netherlands": "NL", "Dutch": "NL",
    "RUB": "RU", "Russia": "RU", "Russian": "RU",
    "Ukraine": "UA", "Ukrainian": "UA",
    "INR": "IN", "India": "IN", "Indian": "IN",
    "BRL": "BR", "Brazil": "BR",
    "MXN": "MX", "Mexico": "MX",
    "TRY": "TR", "Turkey": "TR",
    "Saudi Arabia": "SA",
}

EXTRACT_JS = """
() => {
  const items = Array.from(document.querySelectorAll('.headline-item[data-headlineid]'));
  return items.map(item => {
    const media = item.querySelector('.media');
    const classList = media ? Array.from(media.classList) : [];
    const titleEl = item.querySelector('.headline-title-nolink, .headline-title a');
    const timeEl = item.querySelector('.time');
    const labelEls = Array.from(item.querySelectorAll('.news-label'));
    const socialNav = item.querySelector('.social-nav');
    return {
      id: item.getAttribute('data-headlineid'),
      title: titleEl ? titleEl.textContent.trim() : '',
      time: timeEl ? timeEl.textContent.trim() : '',
      active: classList.includes('active'),
      critical: classList.includes('active-critical'),
      labels: labelEls.map(l => l.textContent.trim()).filter(Boolean),
      link: socialNav ? socialNav.getAttribute('data-link') : null,
    };
  });
}
"""

def _translate_sync(text):
    if not text:
        return text
    try:
        resp = requests.get(
            "https://api.mymemory.translated.net/get",
            params={"q": text[:490], "langpair": "en|fr"},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        translated = data.get("responseData", {}).get("translatedText")
        if translated and "MYMEMORY WARNING" not in translated.upper():
            return translated
    except Exception as exc:
        print(f"Erreur traduction: {exc}", file=sys.stderr)
    return text

async def translate_to_fr(text):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _translate_sync, text)

def detect_countries(labels, title=""):
    countries = []
    for label in labels:
        code = LABEL_TO_COUNTRY.get(label)
        if code and code not in countries:
            countries.append(code)
    for pattern, code in TITLE_KEYWORDS:
        if code not in countries and pattern.search(title):
            countries.append(code)
    return countries[:3]

def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}

def save_state(state):
    if len(state) > 4000:
        state = dict(list(state.items())[-2000:])
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def _impact_badge(headline):
    if headline["critical"]:
        return "🆘 Alerte Critique"
    elif headline["active"]:
        return "🔴 Alerte Marche"
    else:
        return "🔵 Actualite"

def _impact_color(headline):
    if headline["critical"] or headline["active"]:
        return RED_COLOR
    return GREY_COLOR

async def build_embed(headline):
    countries = detect_countries(headline["labels"], headline["title"])
    flags = " ".join(dict.fromkeys(MARKETS[c]["flag"] for c in countries if c in MARKETS)) or "🌍"

    title_fr = await translate_to_fr(headline["title"])

    pairs, indices, other = set(), set(), set()
    for code in countries:
        info = MARKETS.get(code)
        if not info:
            continue
        pairs.update(info.get("pairs", []))
        indices.update(info.get("indices", []))
        other.update(info.get("other", []))

    assets = sorted(pairs) + sorted(other)
    markets = sorted(indices)

    description = f"**{title_fr}**"
    if assets:
        shown = assets[:6]
        assets_line = " · ".join(shown)
        if len(assets) > 6:
            assets_line += " …"
        description += f"\n\n💹 **Actifs pouvant etre impactes :** {assets_line}"
    if markets:
        shown = markets[:6]
        markets_line = " · ".join(shown)
        if len(markets) > 6:
            markets_line += " …"
        description += f"\n📊 **Marches pouvant etre impactes :** {markets_line}"

    embed = discord.Embed(
        title=f"{flags}  {_impact_badge(headline)}",
        description=description[:4096],
        color=_impact_color(headline),
        timestamp=datetime.now(timezone.utc),
    )
    return embed

intents = discord.Intents.default()
bot = discord.Client(intents=intents)

EXIT_CODE = 0
scanner_started = False
scanner_task = None

async def send_alert(text):
    try:
        channel = bot.get_channel(ALERT_CHANNEL_ID) or await bot.fetch_channel(ALERT_CHANNEL_ID)
        await channel.send(f"⚠️ **Alerte bot** : {text}"[:2000])
    except Exception as exc:
        print(f"Impossible d'envoyer l'alerte Discord: {exc}", file=sys.stderr)

async def fatal_shutdown(reason):
    # Code de sortie non nul : le run GitHub apparait en echec au lieu de "reussi".
    global EXIT_CODE
    EXIT_CODE = 1
    print(f"ERREUR FATALE: {reason}", file=sys.stderr)
    await send_alert(f"{reason} — redemarrage automatique en cours.")
    await bot.close()

def on_scanner_done(task):
    if task.cancelled():
        return
    exc = task.exception()
    if exc is None:
        return
    traceback.print_exception(exc)
    bot.loop.create_task(fatal_shutdown(f"la boucle de scan s'est arretee ({exc!r})"))

async def safe_wait_ready(page):
    try:
        await page.wait_for_selector(".headline-item[data-headlineid]", timeout=25000)
    except Exception:
        pass
    await page.wait_for_timeout(4000)

async def process_tick(page, channel, state, tick):
    if tick % RELOAD_EVERY_N_TICKS == 0:
        await page.reload(wait_until="domcontentloaded", timeout=45000)
        await safe_wait_ready(page)
        print("Page rechargee (rafraichissement periodique).")

    headlines = await page.evaluate(EXTRACT_JS)
    if tick % 15 == 0:
        newest = headlines[0]["time"] if headlines else "?"
        print(f"Battement (tick {tick}): {len(headlines)} actualites visibles, plus recente a {newest}.")

    new_headlines = []
    for headline in headlines:
        if not headline["id"] or not headline["title"]:
            continue
        if not (headline["active"] or headline["critical"]):
            continue
        if f"headline_{headline['id']}" in state:
            continue
        new_headlines.append(headline)

    if new_headlines:
        # Le fil affiche les plus recentes en premier ; on les envoie dans l'ordre chronologique.
        new_headlines.reverse()
        embeds = await asyncio.gather(*(build_embed(h) for h in new_headlines))
        for headline, embed in zip(new_headlines, embeds):
            await channel.send(embed=embed)
            state[f"headline_{headline['id']}"] = True
            save_state(state)
            print(f"Envoye: {headline['title'][:80]}")

    return len(headlines)

async def reconnect_browser(playwright_ctx):
    browser = await playwright_ctx.chromium.launch()
    try:
        page = await browser.new_page(user_agent=UA, viewport={"width": 1366, "height": 900})
        await page.goto(URL, wait_until="domcontentloaded", timeout=45000)
        await safe_wait_ready(page)
    except Exception:
        await browser.close()
        raise
    return browser, page

async def launch_with_retries(playwright_ctx):
    for attempt in range(1, BROWSER_LAUNCH_ATTEMPTS + 1):
        try:
            return await reconnect_browser(playwright_ctx)
        except Exception as exc:
            print(f"Echec lancement navigateur (essai {attempt}/{BROWSER_LAUNCH_ATTEMPTS}): {exc!r}", file=sys.stderr)
            if attempt < BROWSER_LAUNCH_ATTEMPTS:
                await asyncio.sleep(min(60, 10 * attempt))
    raise RuntimeError(f"navigateur impossible a lancer apres {BROWSER_LAUNCH_ATTEMPTS} essais")

async def scanning_loop():
    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID) or await bot.fetch_channel(CHANNEL_ID)
    state = load_state()

    playwright_ctx = await async_playwright().start()
    browser, page = await launch_with_retries(playwright_ctx)

    start_time = time.monotonic()
    last_healthy = time.monotonic()
    alerted = False
    tick = 0
    print("Boucle de scan demarree (actualites FinancialJuice, filtrage actif : important/critique uniquement).")

    while time.monotonic() - start_time < MAX_RUNTIME_SECONDS:
        tick += 1
        try:
            visible = await asyncio.wait_for(process_tick(page, channel, state, tick), timeout=60)
            if visible > 0:
                last_healthy = time.monotonic()
                if alerted:
                    alerted = False
                    await send_alert("le bot lit de nouveau les actualites normalement (retour a la normale).")
            else:
                print(f"Aucune actualite visible (tick {tick}).", file=sys.stderr)
        except Exception as exc:
            print(f"Erreur boucle de scan (tick {tick}): {exc!r}", file=sys.stderr)
            try:
                await browser.close()
            except Exception:
                pass
            await asyncio.sleep(5)
            browser, page = await launch_with_retries(playwright_ctx)

        unhealthy_for = time.monotonic() - last_healthy
        if unhealthy_for > UNHEALTHY_ALERT_SECONDS and not alerted:
            alerted = True
            await send_alert(
                f"aucune actualite lue depuis {int(unhealthy_for // 60)} min "
                "(site inaccessible, bloque ou structure modifiee). Je continue d'essayer."
            )

        await asyncio.sleep(POLL_INTERVAL_SECONDS)

    try:
        await browser.close()
        await playwright_ctx.stop()
    except Exception:
        pass

    print("Duree maximale atteinte, arret propre pour relance par le workflow.")
    await bot.close()

async def send_demo_message():
    channel = bot.get_channel(CHANNEL_ID) or await bot.fetch_channel(CHANNEL_ID)
    demo_headline = {
        "id": "demo",
        "title": "Fed's Powell: The economy remains resilient despite ongoing uncertainty",
        "time": "14:30 Sep 22",
        "active": True,
        "critical": False,
        "labels": ["USD", "United States", "Fed"],
        "link": None,
    }
    embed = await build_embed(demo_headline)
    embed.set_footer(text="Exemple de demonstration")
    await channel.send(embed=embed)
    print("Message de demonstration envoye.")

async def clean_channel():
    channel = bot.get_channel(CHANNEL_ID) or await bot.fetch_channel(CHANNEL_ID)
    deleted_total = 0
    while True:
        deleted = await channel.purge(limit=100)
        deleted_total += len(deleted)
        if len(deleted) < 100:
            break
    print(f"Nettoyage termine : {deleted_total} message(s) supprime(s).")

@bot.event
async def on_ready():
    global scanner_started, scanner_task
    print(f"Connecte en tant que {bot.user}")
    # on_ready peut se redeclencher apres une reconnexion Discord : un seul scanner.
    if scanner_started:
        return
    scanner_started = True
    if os.environ.get("CLEAN_CHANNEL") == "true":
        try:
            await clean_channel()
        except Exception as exc:
            print(f"Erreur nettoyage du salon: {exc}", file=sys.stderr)
        await bot.close()
        return
    if os.environ.get("SEND_DEMO") == "true":
        try:
            await send_demo_message()
        except Exception as exc:
            print(f"Erreur envoi message de demonstration: {exc}", file=sys.stderr)
    # Reference globale : asyncio ne garde qu'une reference faible vers les taches.
    scanner_task = bot.loop.create_task(scanning_loop())
    scanner_task.add_done_callback(on_scanner_done)

if __name__ == "__main__":
    bot.run(BOT_TOKEN)
    sys.exit(EXIT_CODE)
