#!/usr/bin/env python3
"""
t212_debug.py - Diagnóstico T212 API
Corre uma vez para ver a resposta exata da API.
"""
import os, requests

KEY = os.environ.get("T212_API_KEY", "")
print(f"Key length    : {len(KEY)}")
print(f"Key preview   : {KEY[:6]}...{KEY[-4:]}")
print(f"Key has space : {' ' in KEY}")
print(f"Key has newline: {'chr(10)' in repr(KEY)}")
print()

for base in [
    "https://live.trading212.com/api/v0",
    "https://demo.trading212.com/api/v0"
]:
    print(f"--- {base} ---")
    try:
        r = requests.get(
            f"{base}/equity/portfolio",
            headers={"Authorization": KEY.strip()},
            timeout=15
        )
        print(f"  Status : {r.status_code}")
        print(f"  Body   : {r.text[:500]}")
    except Exception as e:
        print(f"  ERRO: {e}")
    print()
