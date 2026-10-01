#!/usr/bin/env python3
"""Busca productos de moda ganadores en la Biblioteca de anuncios de Meta (API ads_archive).

Recorre los 8 países y las palabras clave traducidas, aplica las reglas de días /
alcance / anuncios activos y guarda los candidatos en candidatos.json.

Requisitos:
  - Red con acceso a graph.facebook.com
  - Variable de entorno META_TOKEN con un token de acceso de la Ad Library API
    (https://www.facebook.com/ads/library/api, requiere verificar identidad en Meta)

Uso:
  META_TOKEN=... python3 buscar_ganadores.py            # todos los países
  META_TOKEN=... python3 buscar_ganadores.py NL DE      # solo algunos
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime

API = "https://graph.facebook.com/v21.0/ads_archive"
FIELDS = ",".join([
    "id", "page_id", "page_name", "ad_delivery_start_time", "ad_snapshot_url",
    "ad_creative_link_captions", "ad_creative_link_titles", "ad_creative_bodies",
    "eu_total_reach",
])

KEYWORDS = {
    "NL": ["uitverkoop eindigt binnenkort", "50% korting", "gratis verzending", "tijdelijke uitverkoop",
           "60 dagen", "jurk 50%", "jas 50%", "trui 50%", "laarzen 50%", "amsterdam.nl", "amsterdam.nl/products/"],
    "IT": ["i saldi finiscono presto", "50% di sconto", "spedizione gratuita", "saldi a tempo limitato",
           "60 giorni", "vestito 50%", "cappotto 50%", "maglione 50%", "stivali 50%", "milano.com", "milano.it"],
    "SE": ["rean slutar snart", "50% rabatt", "fri frakt", "tidsbegränsad rea", "60 dagar",
           "klänning 50%", "kappa 50%", "stickad tröja 50%", "stövlar 50%", "stockholm.se", "stockholm.com"],
    "FI": ["ale päättyy pian", "50% alennus", "ilmainen toimitus", "rajoitetun ajan ale", "60 päivää",
           "mekko 50%", "takki 50%", "neule 50%", "saappaat 50%", "helsinki.fi", "helsinki.com"],
    "DE": ["Sale endet bald", "50% Rabatt", "kostenloser Versand", "zeitlich begrenzter Sale", "60 Tage",
           "Kleid 50%", "Mantel 50%", "Pullover 50%", "Stiefel 50%", "berlin.de", "berlin.com"],
    "AT": ["Sale endet bald", "50% Rabatt", "gratis Versand", "nur für kurze Zeit", "60 Tage",
           "Kleid 50%", "Mantel 50%", "Pullover 50%", "Stiefel 50%", "wien.at", "wien.com"],
    "ES": ["las rebajas terminan pronto", "50% de descuento", "envío gratis", "rebajas por tiempo limitado",
           "60 días", "vestido 50%", "abrigo 50%", "jersey 50%", "botas 50%", "madrid.es", "madrid.com"],
    "FR": ["les soldes se terminent bientôt", "50% de réduction", "livraison gratuite", "soldes à durée limitée",
           "60 jours", "robe 50%", "manteau 50%", "pull 50%", "bottes 50%", "paris.fr", "paris.com"],
}

SHORT_REACH = {3: 15000, 4: 20000, 5: 30000, 6: 40000, 7: 50000}


def reach_min(days):
    if days < 3:
        return None
    if days < 7:
        return SHORT_REACH[days]
    if days < 14:
        return 50000
    if days < 21:
        return 100000
    return 200000


def get(params, token, retries=4):
    params = {**params, "access_token": token}
    url = API + "?" + urllib.parse.urlencode(params)
    for i in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            if e.code in (429, 500, 503) and i < retries - 1:
                time.sleep(2 ** (i + 1))
                continue
            raise SystemExit(f"Error {e.code} de la API: {body[:400]}")
        except urllib.error.URLError:
            if i < retries - 1:
                time.sleep(2 ** (i + 1))
                continue
            raise


def search(cc, term, token, max_ads=300):
    params = {
        "search_terms": term, "ad_reached_countries": json.dumps([cc]), "ad_active_status": "ACTIVE",
        "ad_type": "ALL", "search_type": "KEYWORD_UNORDERED", "fields": FIELDS, "limit": 100,
    }
    ads, after = [], None
    while len(ads) < max_ads:
        if after:
            params["after"] = after
        data = get(params, token)
        ads += data.get("data", [])
        after = data.get("paging", {}).get("cursors", {}).get("after")
        if not after or not data.get("paging", {}).get("next"):
            break
    return ads


def count_active_ads(page_id, cc, token, cap=200):
    params = {
        "search_page_ids": json.dumps([page_id]), "ad_reached_countries": json.dumps([cc]),
        "ad_active_status": "ACTIVE", "ad_type": "ALL", "fields": "id", "limit": 100,
    }
    n, after = 0, None
    while n < cap:
        if after:
            params["after"] = after
        data = get(params, token)
        n += len(data.get("data", []))
        after = data.get("paging", {}).get("cursors", {}).get("after")
        if not after or not data.get("paging", {}).get("next"):
            break
    return n


def days_active(start):
    return (date.today() - datetime.fromisoformat(start[:10]).date()).days


def main():
    token = os.environ.get("META_TOKEN")
    if not token:
        raise SystemExit("Falta META_TOKEN (token de la Ad Library API).")
    countries = [c.upper() for c in sys.argv[1:]] or list(KEYWORDS)
    page_ads_cache = {}
    candidates = {}

    for cc in countries:
        for term in KEYWORDS[cc]:
            ads = search(cc, term, token)
            print(f"{cc} · {term!r}: {len(ads)} anuncios activos", file=sys.stderr)
            for ad in ads:
                days = days_active(ad["ad_delivery_start_time"])
                reach = ad.get("eu_total_reach") or 0
                if days > 30:
                    continue
                key = (cc, ad["page_id"])
                if days >= 7:
                    # Búsqueda estándar: alcance mínimo según días + 25 anuncios activos
                    if reach < reach_min(days):
                        continue
                    if key not in page_ads_cache:
                        page_ads_cache[key] = count_active_ads(ad["page_id"], cc, token)
                    active = page_ads_cache[key]
                    if active < 25:
                        continue
                    mode = "estándar"
                else:
                    # Activo poco tiempo: alcance opcional como filtro de calidad.
                    # Tienda ≥150 productos y top 15% en PPSpy se comprueban a mano.
                    rm = reach_min(days)
                    if rm and reach < rm:
                        continue
                    active = page_ads_cache.get(key)
                    mode = "poco tiempo"
                prev = candidates.get(ad["id"])
                if prev and prev["reach"] >= reach:
                    continue
                candidates[ad["id"]] = {
                    "country": cc, "keyword": term, "mode": mode, "days": days, "reach": reach,
                    "reach_per_day": round(reach / max(days, 1)),
                    "page_name": ad.get("page_name"), "page_id": ad["page_id"], "active_ads_page": active,
                    "start_date": ad["ad_delivery_start_time"][:10],
                    "landing": (ad.get("ad_creative_link_captions") or [None])[0],
                    "title": (ad.get("ad_creative_link_titles") or [None])[0],
                    "text": ((ad.get("ad_creative_bodies") or [""])[0] or "")[:200],
                    "ad_url": f"https://www.facebook.com/ads/library/?id={ad['id']}",
                }

    # Un anuncio por tienda+título: nos quedamos con el de mayor alcance
    best = {}
    for c in candidates.values():
        k = (c["country"], c["page_id"], (c["title"] or "").lower())
        if k not in best or c["reach"] > best[k]["reach"]:
            best[k] = c
    out = sorted(best.values(), key=lambda c: c["reach_per_day"], reverse=True)
    with open("candidatos.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"{len(out)} candidatos guardados en candidatos.json", file=sys.stderr)


if __name__ == "__main__":
    main()
