"""Google Search Console — READ-ONLY rapport via Maxims eigen user-OAuth (geen service-account).

Vereist in .env: GADS_CLIENT_ID + GADS_CLIENT_SECRET + GSC_REFRESH_TOKEN (zie gsc_auth.py).
Gebruikt uitsluitend lees-endpoints (sites.list + searchAnalytics.query). Geen mutaties.

Gebruik:
    py scripts/gsc_report.py --sites                                          # toegankelijke properties
    py scripts/gsc_report.py jjsign.nl 2026-02-01 2026-04-30 2026-05-01 2026-06-25   # voor/na
"""
import json
import re
import sys
import urllib.parse

from _common import env, http

TOKEN_URL = "https://oauth2.googleapis.com/token"
BASE = "https://searchconsole.googleapis.com/webmasters/v3"


def gsc_token():
    cid, secret, rt = env("GADS_CLIENT_ID"), env("GADS_CLIENT_SECRET"), env("GSC_REFRESH_TOKEN")
    if not rt:
        return None, "GSC_REFRESH_TOKEN ontbreekt in .env — draai eerst (Maxim): py scripts/gsc_auth.py"
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
    """Mogelijke siteUrl-vormen voor een domein (domain-property + URL-prefix, www en kaal)."""
    d = re.sub(r"^https?://", "", domain.strip()).rstrip("/")
    bare = re.sub(r"^www\.", "", d)
    return [f"sc-domain:{bare}", f"https://www.{bare}/", f"https://{bare}/", f"http://www.{bare}/", f"http://{bare}/"]


def resolve_site(token, domain):
    """Kies de siteUrl-vorm waartoe dit account toegang heeft (match tegen sites.list)."""
    sites, err = list_sites(token)
    if err:
        return None, err
    cands = _candidates(domain)
    for c in cands:
        if c in sites:
            return c, None
    # ruimer: domein-match op willekeurige vorm
    bare = re.sub(r"^www\.", "", re.sub(r"^https?://", "", domain)).rstrip("/")
    for s in sites:
        if bare in (s or ""):
            return s, None
    return None, f"geen GSC-property gevonden voor '{domain}' (toegang? toegankelijk: {sites})"


def query(token, site_url, start, end, dimensions=None, row_limit=25):
    payload = {"startDate": start, "endDate": end, "rowLimit": row_limit}
    if dimensions:
        payload["dimensions"] = dimensions
    enc = urllib.parse.quote(site_url, safe="")
    status, body = http("POST", f"{BASE}/sites/{enc}/searchAnalytics/query",
                        headers={**_auth(token), "Content-Type": "application/json"}, data=payload)
    if status != 200:
        return None, f"searchAnalytics HTTP {status}: {body[:300]}"
    return json.loads(body), None


# ---------- pure parsers (testbaar zonder netwerk) ----------
def parse_totals(rep):
    """Totalen uit een dimensieloze respons (één rij) -> {clicks, impressions, ctr, position}."""
    rows = (rep or {}).get("rows", [])
    if not rows:
        return {"clicks": 0, "impressions": 0, "ctr": 0.0, "position": 0.0}
    r = rows[0]
    return {"clicks": r.get("clicks", 0), "impressions": r.get("impressions", 0),
            "ctr": r.get("ctr", 0.0), "position": r.get("position", 0.0)}


def parse_rows(rep):
    """Rijen met dimensie(s) -> [{key, clicks, impressions, ctr, position}] (key = eerste dimensie)."""
    out = []
    for r in (rep or {}).get("rows", []):
        out.append({"key": (r.get("keys") or [""])[0], "clicks": r.get("clicks", 0),
                    "impressions": r.get("impressions", 0), "ctr": r.get("ctr", 0.0),
                    "position": r.get("position", 0.0)})
    return out


def _delta_block(before, after):
    out = {}
    for k in ("clicks", "impressions", "ctr", "position"):
        b, a = before.get(k, 0), after.get(k, 0)
        out[k] = {"before": b, "after": a, "delta": a - b,
                  "delta_pct": ((a - b) / b * 100) if b else None}
    return out


def compare(token, site_url, before, after, top=15):
    """Voor/na: totalen + delta, plus top-queries en top-pagina's van de NA-periode."""
    bt, e1 = query(token, site_url, before[0], before[1])
    if e1:
        return None, e1
    at, e2 = query(token, site_url, after[0], after[1])
    if e2:
        return None, e2
    tq, _ = query(token, site_url, after[0], after[1], dimensions=["query"], row_limit=top)
    tp, _ = query(token, site_url, after[0], after[1], dimensions=["page"], row_limit=top)
    return {"site": site_url, "totals": _delta_block(parse_totals(bt), parse_totals(at)),
            "top_queries": parse_rows(tq or {}), "top_pages": parse_rows(tp or {})}, None


def main():
    a = [x for x in sys.argv[1:] if not x.startswith("--")]
    token, err = gsc_token()
    if err:
        print("FOUT:", err); return 1
    if "--sites" in sys.argv or not a:
        sites, err = list_sites(token)
        print("FOUT:", err) if err else print("GSC-properties:\n  " + "\n  ".join(sites or ["(geen)"]))
        return 0 if not err else 1
    domain = a[0]
    site, err = resolve_site(token, domain)
    if err:
        print("FOUT:", err); return 1
    print(f"Property: {site}")
    if len(a) >= 5:
        cmp, err = compare(token, site, (a[1], a[2]), (a[3], a[4]))
        if err:
            print("FOUT:", err); return 1
        print(f"\n=== Search Console VOOR/NA ({a[1]}..{a[2]} vs {a[3]}..{a[4]}) ===")
        for k, d in cmp["totals"].items():
            pct = f"{d['delta_pct']:+.1f}%" if d["delta_pct"] is not None else "—"
            b, av = d["before"], d["after"]
            if k == "ctr":
                print(f"  CTR:          {b*100:.2f}% -> {av*100:.2f}%   ({pct})")
            elif k == "position":
                print(f"  Gem. positie: {b:.1f} -> {av:.1f}   (lager = beter)")
            else:
                print(f"  {k.capitalize():<12} {b:,.0f} -> {av:,.0f}   ({pct})")
        print("\n  Top-queries (na):")
        for r in cmp["top_queries"][:10]:
            print(f"   {r['clicks']:>4} klik | {r['impressions']:>6} imp | pos {r['position']:.1f} | {r['key'][:50]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
