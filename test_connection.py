"""
Script de diagnóstico — corre com: python test_connection.py
Não usa o bot/ para evitar contaminar os logs.
"""
import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("T212_API_KEY_DEMO", "")
BASE_URL = "https://demo.trading212.com/api/v0"

if not API_KEY:
    print("❌ T212_API_KEY_DEMO não encontrada no .env")
    exit(1)

endpoints = [
    "/equity/account/cash",
    "/equity/portfolio",
    "/equity/account/info",
]

for ep in endpoints:
    t0 = time.time()
    try:
        r = requests.get(
            BASE_URL + ep,
            headers={"Authorization": API_KEY},
            timeout=15,
        )
        elapsed = round((time.time() - t0) * 1000)
        print(f"{'✅' if r.ok else '❌'} {ep}")
        print(f"   Status: {r.status_code} | Tempo: {elapsed}ms")
        if r.status_code == 401:
            print("   ⚠️  API Key inválida ou expirada")
        elif r.status_code == 403:
            print("   ⚠️  IP bloqueado ou permissões insuficientes")
        elif r.ok:
            print(f"   Body (100 chars): {r.text[:100]}")
    except requests.exceptions.ReadTimeout:
        print(f"⏱  {ep} → READ TIMEOUT (>15s)")
    except requests.exceptions.ConnectTimeout:
        print(f"⏱  {ep} → CONNECT TIMEOUT")
    except requests.exceptions.ConnectionError as e:
        print(f"🔌 {ep} → CONNECTION ERROR: {e}")
    print()
