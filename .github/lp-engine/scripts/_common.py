"""Gedeelde helpers voor de Adsvantage Fleet (alleen Python-stdlib)."""
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Windows-console praat standaard cp1252 en crasht op emoji bij print(). Forceer utf-8.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - sommige streams ondersteunen reconfigure niet
        pass

ROOT = Path(__file__).resolve().parent.parent


def load_env(path=None):
    """Laad KEY=VALUE-regels uit .env in os.environ (overschrijft bestaande niet).

    Stript inline comments (` # …`) en omringende quotes: `POLL=60  # elke minuut` gaf anders
    letterlijk "60  # elke minuut" -> int() crasht en de hele runner ligt eruit."""
    path = Path(path) if path else (ROOT / ".env")
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]                    # gequote waarde: comment-strip overslaan (mag # bevatten)
        elif val.startswith("#"):
            val = ""                           # `KEY=  # comment` -> lege waarde, niet de comment-tekst
        else:
            val = re.split(r"\s+#", val, 1)[0].rstrip()   # ` # comment` achter de waarde weghalen
        if key and key not in os.environ:
            os.environ[key] = val


def env(key, default=None):
    val = os.environ.get(key)
    return val if val not in (None, "") else default


def write_env_var(key, value, path=None):
    """Schrijf KEY=value naar .env zonder de waarde ooit te printen.

    Refresh-tokens die naar stdout gaan belanden permanent in de terminal-scrollback,
    de shell-history én in Claude-transcripts. Auth-scripts schrijven daarom direct
    naar .env en tonen alleen de lengte. Bestaande regel wordt vervangen."""
    path = Path(path) if path else (ROOT / ".env")
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    out, found = [], False
    for ln in lines:
        if ln.startswith(f"{key}="):
            out.append(f"{key}={value}"); found = True
        else:
            out.append(ln)
    if not found:
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.environ[key] = value
    return len(value)


def slug(text):
    """Maak een veilige map-/sitenaam van een bedrijfsnaam."""
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s or "site"


_CTX = ssl.create_default_context()


def http(method, url, headers=None, data=None, timeout=30, max_bytes=None):
    """Doe een HTTP-call. Retourneert (status_code, body_text). status 0 = netwerkfout.

    max_bytes begrenst hoeveel van de response-body wordt ingelezen. Belangrijk voor het scannen van
    VREEMDE websites (prospects/klanten): een enkele reuzenpagina liet `body.lower()` een MemoryError
    gooien die een hele klant-audit sloopte. API-calls laten max_bytes leeg (ongelimiteerd)."""
    headers = dict(headers or {})
    body = None
    if data is not None:
        if isinstance(data, (dict, list)):
            body = json.dumps(data).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
        elif isinstance(data, str):
            body = data.encode("utf-8")
        else:
            body = data
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
            raw = r.read(int(max_bytes)) if max_bytes else r.read()
            return r.status, raw.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001 - netwerk/SSL/timeout netjes teruggeven
        return 0, f"{type(e).__name__}: {e}"


_GET_CACHE = None  # None = uit (standaard). Een dict = aan: dedupe identieke GETs binnen één run.


def enable_get_cache():
    """Zet een in-process GET-cache aan (voor audits die dezelfde pagina's meermaals ophalen).

    Voorkomt rate-limiting (429) doordat elke URL hooguit één keer per run wordt opgehaald. Alleen
    geslaagde responses worden gecachet, zodat een tijdelijke fout opnieuw geprobeerd kan worden.
    """
    global _GET_CACHE
    _GET_CACHE = {}


def get(url, headers=None, timeout=30, max_bytes=None):
    if _GET_CACHE is not None and url in _GET_CACHE:
        return _GET_CACHE[url]
    res = http("GET", url, headers=headers, timeout=timeout, max_bytes=max_bytes)
    if _GET_CACHE is not None and res[0] and res[0] < 400:
        _GET_CACHE[url] = res
    return res


def post_json(url, obj, headers=None, timeout=30):
    return http("POST", url, headers=headers, data=obj, timeout=timeout)


def download(url, out_path, headers=None, timeout=30):
    """Download bytes binair-veilig naar out_path (NIET via http(), dat utf-8 decodeert en bytes sloopt).

    Voor o.a. Google-foto's die in een demo-site lokaal opgeslagen worden. -> True bij succes."""
    h = {"User-Agent": "AdsvantageBot/1.0"}
    h.update(headers or {})
    try:
        req = urllib.request.Request(url, headers=h, method="GET")
        with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
            data = r.read()
        p = Path(out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return True
    except Exception:  # noqa: BLE001 - netwerk/SSL/timeout: foto overslaan, nooit de bouw breken
        return False


# Logische kanaalnamen uit de scripts (channel="x") -> echte, geconsolideerde Slack-kanalen (7 stuks).
CHANNEL_MAP = {
    "ads": "ads",
    "tracking": "tracking",
    "leadgen": "leadgen", "crm": "leadgen",
    "cro": "audits", "seo": "audits", "geo": "audits",
    "local": "audits", "consult": "audits", "demos": "audits",
    "content": "content",
    "klanten": "klanten",                    # overzichtskanaal: 1 regel per klant (roll-up)
    "ceo": "fleet", "boardroom": "fleet", "coach": "fleet",
    "fleet": "fleet", "growth": "fleet", "n8n": "fleet",
}
DEFAULT_CHANNEL = "fleet"
TARGET_CHANNELS = sorted(set(CHANNEL_MAP.values()))


def real_channel(channel):
    """Logische kanaalnaam -> echt Slack-kanaal (zonder #). Onbekend -> eigen naam, leeg -> default.

    Per-klant-kanalen (`klant-<slug>`, zie client_channel) staan NIET in de map en vallen daardoor
    op hun eigen naam terug — precies de bedoeling (elk zijn eigen kanaal)."""
    name = (channel or "").lower().strip()
    return CHANNEL_MAP.get(name, name or DEFAULT_CHANNEL)


def client_channel(name):
    """Klantnaam -> vast per-klant Slack-kanaal 'klant-<slug>' (max 80 tekens, Slack-veilig).

    Wordt bij de eerste post automatisch aangemaakt (slack() -> _ensure_channel)."""
    return f"klant-{slug(name)}"[:80]


def channel_id(channel):
    """Publieke helper: echt Slack-kanaal-id voor een (logische/klant)naam, of None.

    Maakt niets aan; leest de (lui gevulde) kanaal-cache. Handig om een klikbare <#ID>-link te bouwen."""
    token = env("SLACK_BOT_TOKEN")
    if not token:
        return None
    return _load_channel_ids(token).get(real_channel(channel))


def _slack_api(token, method, payload):
    st, body = http("POST", f"https://slack.com/api/{method}",
                    headers={"Authorization": f"Bearer {token}",
                             "Content-Type": "application/json; charset=utf-8"},
                    data=payload)
    try:
        return st, json.loads(body)
    except Exception:  # noqa: BLE001
        return st, {"ok": False, "error": (body or "")[:120]}


_SLACK_CHAN_IDS = None  # naam -> channel-id (lui gevuld, 1x per proces)


def _load_channel_ids(token):
    global _SLACK_CHAN_IDS
    if _SLACK_CHAN_IDS is not None:
        return _SLACK_CHAN_IDS
    _SLACK_CHAN_IDS, cursor = {}, ""
    for _ in range(10):                      # paginatie-cap
        url = ("https://slack.com/api/conversations.list?types=public_channel&limit=1000"
               + (f"&cursor={cursor}" if cursor else ""))
        st, body = http("GET", url, headers={"Authorization": f"Bearer {token}"})
        try:
            d = json.loads(body)
        except Exception:  # noqa: BLE001
            break
        for c in d.get("channels", []):
            _SLACK_CHAN_IDS[c["name"]] = c["id"]
        cursor = (d.get("response_metadata") or {}).get("next_cursor") or ""
        if not cursor:
            break
    return _SLACK_CHAN_IDS


def _ensure_channel(token, name):
    """Channel-id voor `name`; maak het kanaal aan als het nog niet bestaat. -> id of None."""
    global _SLACK_CHAN_IDS
    ids = _load_channel_ids(token)
    if name in ids:
        return ids[name]
    st, d = _slack_api(token, "conversations.create", {"name": name})
    if d.get("ok"):
        ids[name] = d["channel"]["id"]
        return ids[name]
    if d.get("error") == "name_taken":       # bestaat al maar niet in cache -> herlaad
        _SLACK_CHAN_IDS = None
        return _load_channel_ids(token).get(name)
    return None


def _slack_bot_post(token, channel, text, blocks):
    real = real_channel(channel)
    cid = _ensure_channel(token, real) or f"#{real}"
    payload = {"channel": cid, "text": text}
    if blocks:
        payload["blocks"] = blocks
    st, d = _slack_api(token, "chat.postMessage", payload)
    return st, ("ok" if d.get("ok") else d.get("error", "?"))


def slack(text, blocks=None, channel=None):
    """Post naar Slack. Met SLACK_BOT_TOKEN: chat.postMessage naar het (geconsolideerde) kanaal op naam,
    en maak het kanaal aan als het nog niet bestaat. Zonder token: val terug op de incoming-webhooks.
    """
    token = env("SLACK_BOT_TOKEN")
    if token:
        return _slack_bot_post(token, channel, text, blocks)
    url = env(f"SLACK_WEBHOOK_{channel.upper()}") if channel else None
    url = url or env("SLACK_WEBHOOK_URL")
    if not url:
        return 0, "SLACK_WEBHOOK_URL ontbreekt"
    payload = {"text": text}
    if blocks:
        payload["blocks"] = blocks
    return post_json(url, payload)


def slack_dm_email(email, text):
    """Stuur een directe Slack-DM naar de persoon met dit e-mailadres (bv. tom@adsvantage.nl).

    Zoekt de Slack-user op via het e-mailadres (vereist scope users:read.email) en post een DM.
    Faalt veilig (no-op + nette melding) als token/scope/gebruiker ontbreekt. -> (status, info).
    """
    token = env("SLACK_BOT_TOKEN")
    if not (token and email):
        return 0, "geen bot-token of e-mail"
    import urllib.parse
    st, body = http("GET", "https://slack.com/api/users.lookupByEmail?email="
                    + urllib.parse.quote(email), headers={"Authorization": f"Bearer {token}"})
    try:
        d = json.loads(body)
    except Exception:  # noqa: BLE001
        return st, "lookup-parsefout"
    uid = (d.get("user") or {}).get("id")
    if not uid:
        return st, d.get("error", "geen Slack-user voor dit e-mailadres")
    st2, d2 = _slack_api(token, "chat.postMessage", {"channel": uid, "text": text})
    return st2, ("ok" if d2.get("ok") else d2.get("error", "?"))


def slack_thread(channel_id, thread_ts, text):
    """Post een antwoord IN een bestaande thread, op een RAW Slack-kanaal-ID (geen naam-resolutie).

    Voor twee-weg Slack: de events-functie levert een kanaal-ID (C…) + thread_ts aan, en NOVA's
    runner meldt het resultaat terug in diezelfde thread. Vereist SLACK_BOT_TOKEN; zonder token een
    nette no-op zodat terugmelden de uitvoering nooit kan laten crashen. -> (status, info).
    """
    token = env("SLACK_BOT_TOKEN")
    if not (token and channel_id and thread_ts):
        return 0, "geen bot-token of thread-context"
    st, d = _slack_api(token, "chat.postMessage",
                       {"channel": channel_id, "thread_ts": thread_ts, "text": text})
    return st, ("ok" if d.get("ok") else d.get("error", "?"))


load_env()
