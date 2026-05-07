#!/usr/bin/env python3
import os, requests

KEY   = os.environ.get("T212_API_KEY", "")
PROXY = os.environ.get("EU_PROXY_URL", "")

print(f"Key length    : {len(KEY)}")
print(f"Key preview   : {KEY[:6]}...{KEY[-4:]}")
print(f"Key has space : {' ' in KEY}")
print(f"Proxy         : {PROXY[:30] if PROXY else 'NENHUM'}...")
print()

proxies = {"http": PROXY, "https": PROXY} if PROXY else None

for base in [
    "https://live.trading212.com/api/v0",
    "https://demo.trading212.com/api/v0"
]:
    print(f"--- {base} ---")
    try:
        r = requests.get(
            f"{base}/equity/portfolio",
            headers={"Authorization": KEY.strip()},
            proxies=proxies,
            timeout=15
        )
        print(f"  Status : {r.status_code}")
        print(f"  Body   : {r.text[:500] or '(vazio)'}")
    except Exception as e:
        print(f"  ERRO: {e}")
    print()
