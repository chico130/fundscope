"""
t212_debug.py — Script de diagnóstico da ligação à API Trading 212 (demo).

Corre no workflow .github/workflows/t212-debug.yml via:
    python t212_debug.py

Verifica:
  1. Presença das variáveis de ambiente necessárias
  2. Autenticação HTTP Basic (T212_API_ID:T212_API_KEY em Base64)
  3. Endpoint /portfolio  (posições actuais)
  4. Endpoint /cash  (saldo disponível)
  5. Endpoint /orders  (ordens abertas)
"""
from __future__ import annotations

import base64
import json
import os
import sys
from datetime import datetime, timezone

import requests

T212_BASE_URL = "https://demo.trading212.com/api/v0"


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_auth() -> tuple[str, str]:
    """Monta o header Authorization a partir das env vars.

    Suporta dois formatos:
      • T212_API_ID + T212_API_KEY  → Basic base64(id:key)   [preferido]
      • T212_API_KEY sozinha         → Bearer key             [legado]
    """
    api_id  = os.environ.get("T212_API_ID", "").strip()
    api_key = os.environ.get("T212_API_KEY", "").strip()

    if api_id and api_key:
        creds  = base64.b64encode(f"{api_id}:{api_key}".encode()).decode()
        return "Authorization", f"Basic {creds}"
    if api_key:
        return "Authorization", api_key  # legado: chave directa
    return "", ""


def _get(path: str, headers: dict) -> dict:
    url = f"{T212_BASE_URL}{path}"
    try:
        r = requests.get(url, headers=headers, timeout=10)
        return {"status": r.status_code, "ok": r.status_code == 200, "body": r.text[:500]}
    except requests.RequestException as exc:
        return {"status": None, "ok": False, "body": str(exc)}


def main() -> int:
    print(f"[{_ts()}] === T212 Debug START ===")

    # 1. Env vars
    api_id  = os.environ.get("T212_API_ID", "")
    api_key = os.environ.get("T212_API_KEY", "")
    proxy   = os.environ.get("EU_PROXY_URL", "")

    print(f"  T212_API_ID  : {'✓ presente' if api_id  else '✗ AUSENTE'}")
    print(f"  T212_API_KEY : {'✓ presente' if api_key else '✗ AUSENTE'}")
    print(f"  EU_PROXY_URL : {'✓ presente' if proxy   else '– não configurado (opcional)'}")

    if not api_key:
        print("[ERRO] T212_API_KEY não configurada — sem credenciais para testar.")
        return 1

    auth_header, auth_value = _build_auth()
    headers = {auth_header: auth_value} if auth_header else {}

    # 2–4. Endpoints
    failed = 0
    for label, path in [
        ("portfolio", "/equity/portfolio"),
        ("cash",      "/equity/account/cash"),
        ("orders",    "/equity/orders"),
    ]:
        result = _get(path, headers)
        status = result["status"]
        ok     = result["ok"]
        mark   = "✓" if ok else "✗"
        print(f"  {mark} {label:<12} HTTP {status}  {result['body'][:120]}")
        if not ok:
            failed += 1

    print(f"[{_ts()}] === T212 Debug END === {'OK' if not failed else f'{failed} falha(s)'}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
