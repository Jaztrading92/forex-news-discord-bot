import asyncio
import json
import os
import re
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
RELOAD_EVERY_N_TICKS = 90  # ~30 min entre deux rechargements complets de la page
MAX_RUNTIME_SECONDS = 5 * 3600 + 30 * 60  # 5h30 puis arret propre pour relance par le workflow

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# imp-1 = rouge (impact eleve), imp-2 = orange (impact moyen)
WATCHED_IMPACTS = {"1", "2"}
IMPACT_LABELS = {
    "1": {"icon": "🔴", "color": 0xE0201B},
    "2": {"icon": "🟠", "color": 0xF0A500},
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

ACRONYM_MAP = {
    "non-farm payrolls": "NFP",
    "non farm payrolls": "NFP",
    "nonfarm payrolls": "NFP",
    "non-farm employment change": "NFP",
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

HASH_ID_RE = re.compile(r"^[0-9a-f]{32}$")
SUFFIX_RE = re.compile(r"([A-Z]{2})$")

LANGUAGES = [
    ("en", "🇬🇧", "English"),
    ("fr", "🇫🇷", "Français"),
    ("es", "🇪🇸", "Español"),
    ("ar", "🇸🇦", "العربية"),
    ("zh", "🇨🇳", "中文"),
    ("hi", "🇮🇳", "हिन्दी"),
    ("pt", "🇵🇹", "Português"),
    ("ru", "🇷🇺", "Русский"),
    ("de", "🇩🇪", "Deutsch"),
    ("ja", "🇯🇵", "日本語"),
]

UI_STRINGS = {
    "impact_high": {"en": "High impact", "fr": "Impact eleve", "es": "Impacto alto", "ar": "تأثير مرتفع", "zh": "高影响", "hi": "उच्च प्रभाव", "pt": "Impacto alto", "ru": "Высокое влияние", "de": "Hohe Auswirkung", "ja": "高インパクト"},
    "impact_medium": {"en": "Medium impact", "fr": "Impact moyen", "es": "Impacto medio", "ar": "تأثير متوسط", "zh": "中等影响", "hi": "मध्यम प्रभाव", "pt": "Impacto medio", "ru": "Среднее влияние", "de": "Mittlere Auswirkung", "ja": "中程度のインパクト"},
    "actual": {"en": "Actual", "fr": "Actuel", "es": "Actual", "ar": "الفعلي", "zh": "实际值", "hi": "वास्तविक", "pt": "Atual", "ru": "Фактический", "de": "Tatsaechlich", "ja": "実際値"},
    "forecast": {"en": "Forecast", "fr": "Prevision", "es": "Prevision", "ar": "التوقعات", "zh": "预测值", "hi": "पूर्वानुमान", "pt": "Previsao", "ru": "Прогноз", "de": "Prognose", "ja": "予測値"},
    "previous": {"en": "Previous", "fr": "Precedent", "es": "Anterior", "ar": "السابق", "zh": "前值", "hi": "पिछला", "pt": "Anterior", "ru": "Предыдущий", "de": "Vorherig", "ja": "前回値"},
    "result": {"en": "Result", "fr": "Resultat", "es": "Resultado", "ar": "النتيجة", "zh": "结果", "hi": "परिणाम", "pt": "Resultado", "ru": "Результат", "de": "Ergebnis", "ja": "結果"},
    "above": {"en": "Above forecast", "fr": "Superieur a la prevision", "es": "Por encima de lo previsto", "ar": "أعلى من المتوقع", "zh": "高于预期", "hi": "पूर्वानुमान से अधिक", "pt": "Acima da previsao", "ru": "Выше прогноза", "de": "Ueber der Prognose", "ja": "予測を上回る"},
    "below": {"en": "Below forecast", "fr": "Inferieur a la prevision", "es": "Por debajo de lo previsto", "ar": "أقل من المتوقع", "zh": "低于预期", "hi": "पूर्वानुमान से कम", "pt": "Abaixo da previsao", "ru": "Ниже прогноза", "de": "Unter der Prognose", "ja": "予測を下回る"},
    "inline": {"en": "In line with forecast", "fr": "Conforme a la prevision", "es": "En linea con lo previsto", "ar": "متوافق مع التوقعات", "zh": "符合预期", "hi": "पूर्वानुमान के अनुरूप", "pt": "Em linha com a previsao", "ru": "Соответствует прогнозу", "de": "Im Rahmen der Prognose", "ja": "予測通り"},
    "na": {"en": "Not comparable", "fr": "Non comparable", "es": "No comparable", "ar": "غير قابل للمقارنة", "zh": "无法比较", "hi": "तुलनीय नहीं", "pt": "Nao comparavel", "ru": "Не сравнимо", "de": "Nicht vergleichbar", "ja": "比較不可"},
    "markets": {"en": "Markets likely affected", "fr": "Marches probablement concernes", "es": "Mercados probablemente afectados", "ar": "الأسواق المتأثرة على الأرجح", "zh": "可能受影响的市场", "hi": "संभावित प्रभावित बाज़ार", "pt": "Mercados provavelmente afetados", "ru": "Вероятно затронутые рынки", "de": "Wahrscheinlich betroffene Maerkte", "ja": "影響を受ける可能性のある市場"},
    "pairs": {"en": "Forex pairs", "fr": "Paires Forex", "es": "Pares de Forex", "ar": "أزواج الفوركس", "zh": "外汇货币对", "hi": "फॉरेक्स जोड़े", "pt": "Pares de Forex", "ru": "Валютные пары", "de": "Forex-Paare", "ja": "為替ペア"},
    "indices": {"en": "Indices", "fr": "Indices", "es": "Indices", "ar": "المؤشرات", "zh": "指数", "hi": "सूचकांक", "pt": "Indices", "ru": "Индексы", "de": "Indizes", "ja": "指数"},
    "other": {"en": "Other", "fr": "Autres", "es": "Otros", "ar": "أخرى", "zh": "其他", "hi": "अन्य", "pt": "Outros", "ru": "Другое", "de": "Sonstiges", "ja": "その他"},
    "currency": {"en": "Currency", "fr": "Devise", "es": "Divisa", "ar": "العملة", "zh": "货币", "hi": "मुद्रा", "pt": "Moeda", "ru": "Валюта", "de": "Waehrung", "ja": "通貨"},
    "translated_via": {"en": "Automatic translation", "fr": "Traduction automatique", "es": "Traduccion automatica", "ar": "ترجمة تلقائية", "zh": "自动翻译", "hi": "स्वचालित अनुवाद", "pt": "Traducao automatica", "ru": "Автоматический перевод", "de": "Automatische Uebersetzung", "ja": "自動翻訳"},
}

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


def detect_country(event_id, title):
    if not HASH_ID_RE.match(event_id):
        m = SUFFIX_RE.search(event_id)
        if m and m.group(1) in MARKETS:
            return m.group(1)
    for keyword, code in KEYWORD_COUNTRY:
        if keyword in title:
            return code
    return None


def apply_common_name(title):
    lower = title.lower()
    for phrase, acronym in ACRONYM_MAP.items():
        if phrase in lower and acronym not in title:
            return f"{title} ({acronym})"
    return title


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
        return "➡️", "na"
    if a > f:
        return "🔼", "above"
    if a < f:
        return "🔽", "below"
    return "➡️", "inline"


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


def build_embed(event, country):
    impact_info = IMPACT_LABELS[event["impact"]]
    market_info = MARKETS.get(country)
    flag = market_info["flag"] if market_info else "🌍"
    currency = market_info["currency"] if market_info else "?"
    cmp_icon, cmp_key = compare_actual(event["actual"], event["forecast"])
    display_title = apply_common_name(event["title"])
    impact_label = "Impact eleve" if event["impact"] == "1" else "Impact moyen"

    embed = discord.Embed(
        title=f"{impact_info['icon']} {flag} {display_title}",
        description=f"**{impact_label}** · Devise **{currency}** · {event['date']} a {event['time']}",
        color=impact_info["color"],
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="📌 Actuel", value=event["actual"] or "-", inline=True)
    embed.add_field(name="🎯 Prevision", value=event["forecast"] or "-", inline=True)
    embed.add_field(name="📜 Precedent", value=event["previous"] or "-", inline=True)
    embed.add_field(name="📊 Resultat", value=f"{cmp_icon} {UI_STRINGS[cmp_key]['fr']}", inline=False)

    if market_info:
        lines = []
        if market_info["pairs"]:
            lines.append("**Paires Forex :** " + ", ".join(market_info["pairs"]))
        if market_info["indices"]:
            lines.append("**Indices :** " + ", ".join(market_info["indices"]))
        if market_info["other"]:
            lines.append("**Autres :** " + ", ".join(market_info["other"]))
        embed.add_field(name="💱 Marches probablement concernes", value="\n".join(lines), inline=False)

    embed.set_footer(text="Source : FinancialJuice · Cliquez un drapeau pour traduire")
    return embed


async def translate_text(text, target_lang):
    if target_lang == "en" or not text:
        return text
    try:
        loop = asyncio.get_event_loop()

        def _call():
            resp = requests.get(
                "https://api.mymemory.translated.net/get",
                params={"q": text, "langpair": f"en|{target_lang}"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["responseData"]["translatedText"]

        return await loop.run_in_executor(None, _call)
    except Exception:
        return text


async def build_translated_embed(source_embed, lang_code):
    lang_flag = next(f for c, f, n in LANGUAGES if c == lang_code)
    icon, flag_orig, original_title = source_embed.title.split(" ", 2)
    strings = {key: values[lang_code] for key, values in UI_STRINGS.items()}

    translated_title = await translate_text(original_title, lang_code)

    is_high = source_embed.color is not None and source_embed.color.value == IMPACT_LABELS["1"]["color"]
    impact_label = strings["impact_high"] if is_high else strings["impact_medium"]

    currency_match = re.search(r"\*\*([A-Z]{3})\*\*", source_embed.description or "")
    currency = currency_match.group(1) if currency_match else "?"

    fields_by_name = {f.name: f.value for f in source_embed.fields}
    actual = next((v for k, v in fields_by_name.items() if "Actuel" in k), "-")
    forecast = next((v for k, v in fields_by_name.items() if "Prevision" in k), "-")
    previous = next((v for k, v in fields_by_name.items() if "Precedent" in k), "-")
    result_raw = next((v for k, v in fields_by_name.items() if "Resultat" in k), "")
    markets_field = next((v for k, v in fields_by_name.items() if "Marches" in k), None)

    if "🔼" in result_raw:
        result_label = f"🔼 {strings['above']}"
    elif "🔽" in result_raw:
        result_label = f"🔽 {strings['below']}"
    elif "➡️" in result_raw:
        result_label = f"➡️ {strings['inline']}"
    else:
        result_label = result_raw

    translated = discord.Embed(
        title=f"{icon} {flag_orig} {translated_title}",
        description=f"**{impact_label}** · {strings['currency']} **{currency}**",
        color=source_embed.color,
    )
    translated.add_field(name=f"📌 {strings['actual']}", value=actual, inline=True)
    translated.add_field(name=f"🎯 {strings['forecast']}", value=forecast, inline=True)
    translated.add_field(name=f"📜 {strings['previous']}", value=previous, inline=True)
    translated.add_field(name=f"📊 {strings['result']}", value=result_label, inline=False)
    if markets_field:
        translated.add_field(name=f"💱 {strings['markets']}", value=markets_field, inline=False)
    translated.set_footer(text=f"{lang_flag} {strings['translated_via']}")
    return translated


class LanguageButton(discord.ui.Button):
    def __init__(self, code, flag):
        super().__init__(style=discord.ButtonStyle.secondary, emoji=flag, custom_id=f"lang:{code}")
        self.code = code

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            source_embed = interaction.message.embeds[0]
            translated = await build_translated_embed(source_embed, self.code)
            await interaction.followup.send(embed=translated, ephemeral=True)
        except Exception as exc:
            await interaction.followup.send(f"Erreur de traduction: {exc}", ephemeral=True)


class LanguageView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        for code, flag, _name in LANGUAGES:
            self.add_item(LanguageButton(code, flag))


intents = discord.Intents.default()
bot = discord.Client(intents=intents)


async def safe_wait_ready(page):
    try:
        await page.wait_for_selector(".div-table-row .event-imp", timeout=25000)
    except Exception:
        pass
    await page.wait_for_timeout(4000)


async def scanning_loop():
    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID) or await bot.fetch_channel(CHANNEL_ID)
    state = load_state()

    playwright_ctx = await async_playwright().start()
    browser = await playwright_ctx.chromium.launch()
    page = await browser.new_page(user_agent=UA, viewport={"width": 1366, "height": 900})
    await page.goto(URL, wait_until="domcontentloaded", timeout=45000)
    await safe_wait_ready(page)

    start_time = time.monotonic()
    tick = 0
    print("Boucle de scan demarree.")

    while time.monotonic() - start_time < MAX_RUNTIME_SECONDS:
        tick += 1
        try:
            if tick % RELOAD_EVERY_N_TICKS == 0:
                await page.reload(wait_until="domcontentloaded", timeout=45000)
                await safe_wait_ready(page)

            events = await page.evaluate(EXTRACT_JS)
            for event in events:
                if event["impact"] not in WATCHED_IMPACTS:
                    continue
                if not event["actual"] or event["actual"] == "-":
                    continue

                key = f"{event['date']}_{event['id']}"
                if state.get(key) == event["actual"]:
                    continue

                state[key] = event["actual"]
                save_state(state)

                country = detect_country(event["id"], event["title"])
                embed = build_embed(event, country)
                await channel.send(embed=embed, view=LanguageView())
                print(f"Envoye: {event['title']} ({event['actual']})")
        except Exception as exc:
            print(f"Erreur boucle de scan: {exc}", file=sys.stderr)
            try:
                await browser.close()
            except Exception:
                pass
            await asyncio.sleep(5)
            try:
                browser = await playwright_ctx.chromium.launch()
                page = await browser.new_page(user_agent=UA, viewport={"width": 1366, "height": 900})
                await page.goto(URL, wait_until="domcontentloaded", timeout=45000)
                await safe_wait_ready(page)
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


@bot.event
async def on_ready():
    bot.add_view(LanguageView())
    print(f"Connecte en tant que {bot.user}")
    bot.loop.create_task(scanning_loop())


if __name__ == "__main__":
    bot.run(BOT_TOKEN)
