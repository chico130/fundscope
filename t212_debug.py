#!/usr/bin/env python3
import os, requests, base64

KEY_ID  = os.environ.get("T212_API_ID", "")
SECRET  = os.environ.get("T212_API_KEY", "")
PROXY   = os.environ.get("EU_PROXY_URL", "")

credentials = base64.b64encode(f"{KEY_ID}:{SECRET}".encode()).decode()
auth_header = f"Basic {credentials}"

print(f"Key ID length  : {len(KEY_ID)}")
print(f"Secret length  : {len(SECRET)}")
print(f"Auth header    : Basic {credentials[:20]}...")
print(f"Proxy          : {PROXY[:10] if PROXY else 'NENHUM'}...")
print()

proxies = {"http": PROXY, "https": PROXY} if PROXY else None

base = "https://live.trading212.com/api/v0"
print(f"--- {base} ---")
try:
    r = requests.get(
        f"{base}/equity/portfolio",
        headers={"Authorization": auth_header},
        proxies=proxies,
        timeout=15
    )
    print(f"  Status : {r.status_code}")
    print(f"  Body   : {r.text[:500] or '(vazio)'}")
except Exception as e:
    print(f"  ERRO: {e}")
