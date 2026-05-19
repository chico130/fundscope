#!/usr/bin/env python3
"""
t212_debug.py — Raw API debug para Trading 212 Demo.
Uso: python t212_debug.py
Mostra o JSON bruto de /equity/portfolio, /equity/account/cash e /equity/account/info.
"""
import json, os, requests
from dotenv import load_dotenv
load_dotenv()

KEY = (
    os.getenv("T212_DEMO_KEY") or
    os.getenv("T212_API_KEY_DEMO") or
    os.getenv("T212_API_KEY") or ""
)
BASE = "https://demo.trading212.com/api/v0"

print(f"Base URL   : {BASE}")
print(f"Key length : {len(KEY)} chars | prefix: {KEY[:8]}..." if KEY else "Key        : NAO ENCONTRADA — adiciona T212_DEMO_KEY ao .env")
print()

if not KEY:
    raise SystemExit(1)

ENDPOINTS = [
    "/equity/portfolio",
    "/equity/account/cash",
    "/equity/account/info",
]

for ep in ENDPOINTS:
    print(f"{'='*60}")
    print(f"GET {ep}")
    print(f"{'='*60}")
    try:
        r = requests.get(f"{BASE}{ep}", headers={"Authorization": KEY}, timeout=15)
        print(f"Status : {r.status_code}")
        try:
            body = r.json()
            print(json.dumps(body, indent=2, ensure_ascii=False))
        except Exception:
            print(r.text[:3000])
    except Exception as e:
        print(f"ERRO: {e}")
    print()
