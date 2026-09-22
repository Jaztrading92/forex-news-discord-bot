import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import discord
import requests
from playwright.async_api import async_playwright

URL = "https://www.financialjuice.com/"
STATE_FILE = Path("state.json")
BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
CHANNEL_ID = int(os.environ["DISCORD_CHANNEL_ID"])

POLL_INTERVAL_SECONDS = 20
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
}

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

def detect_countries(labels):
    countries = []
    for label in labels:
        code = LABEL_TO_COUNTRY.get(label)
        if code and code not in countries:
            countries.append(code)
    return countries

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
    countries = detect_countries(headline["labels"])
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
        key = f"headline_{headline['id']}"
        if key in state:
            continue
        state[key] = True
        new_headlines.append(headline)

    if new_headlines:
        save_state(state)
        # Le fil affiche les plus recentes en premier ; on les envoie dans l'ordre chronologique.
        new_headlines.reverse()
        embeds = await asyncio.gather(*(build_embed(h) for h in new_headlines))
        for headline, embed in zip(new_headlines, embeds):
            await channel.send(embed=embed)
            print(f"Envoye: {headline['title'][:80]}")

async def reconnect_browser(playwright_ctx):
    browser = await playwright_ctx.chromium.launch()
    page = await browser.new_page(user_agent=UA, viewport={"width": 1366, "height": 900})
    await page.goto(URL, wait_until="domcontentloaded", timeout=45000)
    await safe_wait_ready(page)
    return browser, page

async def scanning_loop():
    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID) or await bot.fetch_channel(CHANNEL_ID)
    state = load_state()

    playwright_ctx = await async_playwright().start()
    browser, page = await reconnect_browser(playwright_ctx)

    start_time = time.monotonic()
    tick = 0
    print("Boucle de scan demarree (actualites FinancialJuice, filtrage actif : important/critique uniquement).")

    while time.monotonic() - start_time < MAX_RUNTIME_SECONDS:
        tick += 1
        try:
            await asyncio.wait_for(process_tick(page, channel, state, tick), timeout=60)
        except asyncio.TimeoutError:
            print(f"Erreur boucle de scan (tick {tick}): timeout de 60s depasse, relance du navigateur.", file=sys.stderr)
            try:
                await browser.close()
            except Exception:
                pass
            await asyncio.sleep(5)
            try:
                browser, page = await reconnect_browser(playwright_ctx)
            except Exception as exc2:
                print(f"Echec relance navigateur: {exc2}", file=sys.stderr)
        except Exception as exc:
            print(f"Erreur boucle de scan (tick {tick}): {exc}", file=sys.stderr)
            try:
                await browser.close()
            except Exception:
                pass
            await asyncio.sleep(5)
            try:
                browser, page = await reconnect_browser(playwright_ctx)
            except Exception as exc2:
                print(f"Echec relance navigateur: {exc2}", file=sys.stderr)

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
    print(f"Connecte en tant que {bot.user}")
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
    bot.loop.create_task(scanning_loop())

if __name__ == "__main__":
    bot.run(BOT_TOKEN)
