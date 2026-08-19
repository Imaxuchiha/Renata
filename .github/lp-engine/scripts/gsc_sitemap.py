"""Google Search Console — SITEMAP (her)indienen + status, via Maxims write-OAuth.

Dit is de ENIGE zinvolle schrijfactie die de Search Console API biedt. Er bestaat GEEN API om losse
URL's "ter indexering aan te bieden" (URL Inspection API is read-only; de 'Request indexing'-knop is
UI-only). Sitemap herindienen is de officiele manier om Google te nudgen opnieuw te crawlen.

Vereist in .env: GADS_CLIENT_ID + GADS_CLIENT_SECRET + GSC_REFRESH_TOKEN_WRITE (zie gsc_auth_write.py).

Gebruik:
    py scripts/gsc_sitemap.py --sites                          # toegankelijke properties (token-test)
    py scripts/gsc_sitemap.py acrentallisbon.pt --status       # status van ingediende sitemaps
    py scripts/gsc_sitemap.py acrentallisbon.pt --submit       # sitemap.xml (her)indienen
    py scripts/gsc_sitemap.py acrentallisbon.pt --submit https://acrentallisbon.pt/sitemap.xml
"""
import json
import re
import sys
import urllib.parse

from _common import env, http

TOKEN_URL = "https://oauth2.googleapis.com/token"
BASE = "https://searchconsole.googleapis.com/webmasters/v3"


def gsc_token():
    cid, secret, rt = env("GADS_CLIENT_ID"), env("GADS_CLIENT_SECRET"), env("GSC_REFRESH_TOKEN_WRITE")
    if not rt:
        return None, "GSC_REFRESH_TOKEN_WRITE ontbreekt in .env — draai eerst (Maxim): py scripts/gsc_auth_write.py"
    status, body = http("POST", TOKEN_URL,
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                        data=urllib.parse.urlencode({
                            "client_id": cid, "client_secret": secret,
                            "refresh_token": rt, "grant_type": "refresh_token"}))
    try:
        tok = json.loads(body).get("access_token")
    except Exception:  # noqa: BLE001
        tok = None
    if not tok:
        return None, f"Kon access-token niet vernieuwen (HTTP {status}): {body[:300]}"
    return tok, None


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def list_sites(token):
    status, body = http("GET", f"{BASE}/sites", headers=_auth(token))
    if status != 200:
        return [], f"sites.list HTTP {status}: {body[:200]}"
    try:
        return [s.get("siteUrl") for s in json.loads(body).get("siteEntry", [])], None
    except Exception:  # noqa: BLE001
        return [], "kon sites.list niet parsen"


def _candidates(domain):
    d = re.sub(r"^https?://", "", domain.strip()).rstrip("/")
    bare = re.sub(r"^www\.", "", d)
    return [f"https://{bare}/", f"https://www.{bare}/", f"sc-domain:{bare}",
            f"http://{bare}/", f"http://www.{bare}/"]


def resolve_site(token, domain):
    sites, err = list_sites(token)
    if err:
        return None, err
    for c in _candidates(domain):
        if c in sites:
            return c, None
    bare = re.sub(r"^www\.", "", re.sub(r"^https?://", "", domain)).rstrip("/")
    # Exacte host-match, geen substring: 'site.nl' matchte anders óók de 'mysite.nl'-property
    # en een --submit landde dan op de verkeerde property.
    for s in sites:
        s_bare = re.sub(r"^www\.", "", re.sub(r"^(sc-domain:|https?://)", "", s or "")).rstrip("/")
        if s_bare == bare:
            return s, None
    return None, f"geen GSC-property gevonden voor '{domain}' (toegankelijk: {sites})"


def default_feedpath(site_url, domain):
    """Beste gok voor de sitemap-URL. Bij een URL-prefix property: <property>sitemap.xml;
    bij een sc-domain property: https://<domein>/sitemap.xml."""
    if site_url.startswith("sc-domain:"):
        bare = site_url.split(":", 1)[1]
        return f"https://{bare}/sitemap.xml"
    return site_url.rstrip("/") + "/sitemap.xml"


def submit_sitemap(token, site_url, feedpath):
    enc_site = urllib.parse.quote(site_url, safe="")
    enc_feed = urllib.parse.quote(feedpath, safe="")
    status, body = http("PUT", f"{BASE}/sites/{enc_site}/sitemaps/{enc_feed}", headers=_auth(token))
    if status in (200, 204):
        return True, None
    return False, f"sitemaps.submit HTTP {status}: {body[:300]}"


def sitemap_status(token, site_url):
    enc_site = urllib.parse.quote(site_url, safe="")
    status, body = http("GET", f"{BASE}/sites/{enc_site}/sitemaps", headers=_auth(token))
    if status != 200:
        return None, f"sitemaps.list HTTP {status}: {body[:300]}"
    try:
        return json.loads(body).get("sitemap", []), None
    except Exception:  # noqa: BLE001
        return None, "kon sitemaps.list niet parsen"


def main():
    args = [x for x in sys.argv[1:] if not x.startswith("--")]
    flags = {x for x in sys.argv[1:] if x.startswith("--")}
    token, err = gsc_token()
    if err:
        print("FOUT:", err); return 1

    if "--sites" in flags or not args:
        sites, err = list_sites(token)
        print("FOUT:", err) if err else print("GSC-properties (write-token OK):\n  "
                                              + "\n  ".join(sites or ["(geen)"]))
        return 1 if err else 0

    domain = args[0]
    site, err = resolve_site(token, domain)
    if err:
        print("FOUT:", err); return 1
    print(f"Property: {site}")

    if "--status" in flags:
        sm, err = sitemap_status(token, site)
        if err:
            print("FOUT:", err); return 1
        if not sm:
            print("  (geen sitemaps ingediend)")
        for s in sm:
            print(f"  {s.get('path')}  | laatst opgehaald: {s.get('lastDownloaded','—')}"
                  f" | warnings: {s.get('warnings','0')} | errors: {s.get('errors','0')}")
        return 0

    if "--submit" in flags:
        feed = args[1] if len(args) > 1 else default_feedpath(site, domain)
        # Schrijfactie: toon expliciet wat er waarheen gaat vóór de call (verkeerde property = ruis in GSC).
        print(f"  → indienen op property: {site}")
        ok, err = submit_sitemap(token, site, feed)
        print(f"  sitemap (her)ingediend: {feed}" if ok else f"FOUT: {err}")
        return 0 if ok else 1

    print("Geef --sites, --status of --submit op.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
