"""T212 API contract test — regression suite.

Valida em <30s as premissas empíricas sobre a API T212. Corre antes de cada
deploy crítico (ou em CI nightly) para apanhar regressões de schema antes que
cheguem ao bot em produção.

Cada teste compara o comportamento real ao comportamento esperado documentado
em docs/T212_API_MANUAL.md. Falhas indicam que a API mudou e que o manual +
api_client.py precisam de actualização.

Uso:
  # demo (default — sem risco financeiro)
  PYTHONPATH=. python scripts/t212_contract_test.py
  PYTHONPATH=. python scripts/t212_contract_test.py --env demo

  # live (pre-flight Fase 3 — exige T212_LIVE_API_ID/KEY no .env)
  PYTHONPATH=. python scripts/t212_contract_test.py --env live --i-understand-risk

Exit code 0 = todas as premissas confirmadas; ≠0 = pelo menos uma regressão.
Não toca em posições reais — ordens de teste são canceladas imediatamente.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

# Força UTF-8 no terminal Windows (evita UnicodeEncodeError em prints com símbolos)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import requests

from bot.config import (
    T212_BASE_URL_DEMO,
    T212_BASE_URL_LIVE,
    T212_DEMO_KEY,
    T212_LIVE_KEY,
)


# ---------------------------------------------------------------------------
# Setup — populado dinamicamente em main() consoante --env
# ---------------------------------------------------------------------------

BASE_URL: str = T212_BASE_URL_DEMO
ENV_LABEL: str = "demo"
_session: requests.Session = requests.Session()
_session.headers.update({"Content-Type": "application/json"})

# Ticker de teste para SELL: ARM (assume-se em carteira; testes adaptam-se se não estiver)
SELL_TEST_TICKER = "ARM_US_EQ"
# Ticker de teste para LIMIT BUY: F (Ford, ~$15) — LIMIT a $1 nunca executa
LIMIT_TEST_TICKER = "F_US_EQ"

_REQUIRED_PORTFOLIO_FIELDS = {"ticker", "quantity", "averagePrice", "currentPrice"}
_REQUIRED_CASH_FIELDS      = {"free", "total"}
_REQUIRED_ORDER_FIELDS     = {"id", "ticker", "quantity", "status", "side"}

results: list[tuple[str, bool, str]] = []


def _record(name: str, passed: bool, detail: str = "") -> None:
    results.append((name, passed, detail))
    icon = "✓" if passed else "✗"
    print(f"  {icon} {name}" + (f" — {detail}" if detail else ""))


def _cancel_orders_for(ticker: str) -> None:
    r = _session.get(f"{BASE_URL}/equity/orders", timeout=30)
    if r.status_code != 200:
        return
    for o in r.json():
        if o.get("ticker") == ticker:
            oid = o.get("id")
            if oid:
                _session.delete(f"{BASE_URL}/equity/orders/{oid}", timeout=30)


def _get(path: str):
    time.sleep(1.2)
    return _session.get(f"{BASE_URL}{path}", timeout=30)


def _post(path: str, payload: dict):
    time.sleep(1.5)
    return _session.post(f"{BASE_URL}{path}", json=payload, timeout=30)


def _delete(path: str):
    time.sleep(1.2)
    return _session.delete(f"{BASE_URL}{path}", timeout=30)


# ---------------------------------------------------------------------------
# Test suites
# ---------------------------------------------------------------------------

def test_auth_and_portfolio() -> dict | None:
    """Confirma auth e schema de GET /equity/portfolio. Devolve a primeira posição
    (ou None se vazio) para uso noutros testes."""
    print("\n[1] Auth + GET /equity/portfolio")
    r = _get("/equity/portfolio")
    _record("portfolio HTTP 200", r.status_code == 200, f"HTTP {r.status_code}")
    if r.status_code != 200:
        return None

    body = r.json()
    _record("portfolio body is array", isinstance(body, list))
    if not body:
        _record("portfolio non-empty", False, "carteira vazia — alguns testes serão skipados")
        return None

    pos = body[0]
    missing = _REQUIRED_PORTFOLIO_FIELDS - set(pos.keys())
    _record(
        "portfolio item has required fields",
        not missing,
        f"missing={missing}" if missing else f"sample ticker={pos.get('ticker')}",
    )
    return pos


def test_cash():
    print("\n[2] GET /equity/account/cash")
    r = _get("/equity/account/cash")
    _record("cash HTTP 200", r.status_code == 200, f"HTTP {r.status_code}")
    if r.status_code != 200:
        return
    body = r.json()
    missing = _REQUIRED_CASH_FIELDS - set(body.keys())
    _record("cash has required fields", not missing, f"free={body.get('free')}")


def test_orders_list():
    print("\n[3] GET /equity/orders")
    r = _get("/equity/orders")
    _record("orders HTTP 200", r.status_code == 200, f"HTTP {r.status_code}")
    if r.status_code != 200:
        return
    body = r.json()
    _record("orders body is array", isinstance(body, list), f"n={len(body)}")


def _is_schema_accepted(r: requests.Response) -> tuple[bool, str]:
    """True quando a API aceitou o schema do payload (200 OK, ou 400 com qualquer
    /api-errors/ business-level mas NÃO invalid-request). Devolve (ok, err_type).

    Razão: business errors (min-quantity-exceeded, min-value-exceeded,
    selling-equity-not-owned, etc.) provam que o payload foi parseado e
    aplicado às regras de negócio. Só `invalid-request` indica que o schema
    foi recusado.
    """
    try:
        body = r.json()
    except Exception:
        body = {}
    err_type = body.get("type", "")
    if r.status_code == 200:
        return True, err_type
    if r.status_code == 400 and err_type.startswith("/api-errors/") and not err_type.endswith("invalid-request"):
        return True, err_type
    return False, err_type


def test_market_buy_schema():
    """Confirma que MARKET aceita {ticker, quantity} minimal e REJEITA timeValidity.

    Usa quantity:0.0001 — abaixo do mínimo de qty/valor, devolve 400 business-level.
    Se viesse 400 invalid-request seria regressão de schema.
    """
    print("\n[4] POST /equity/orders/market — schema BUY")

    # 4a: minimal payload válido
    r = _post("/equity/orders/market", {"ticker": SELL_TEST_TICKER, "quantity": 0.0001})
    ok, err_type = _is_schema_accepted(r)
    _record(
        "market minimal payload accepted",
        ok,
        f"HTTP {r.status_code} type={err_type}",
    )
    if r.status_code == 200:
        _cancel_orders_for(SELL_TEST_TICKER)

    # 4b: payload com timeValidity DEVE ser rejeitado com invalid-request (schema)
    r = _post("/equity/orders/market", {
        "ticker": SELL_TEST_TICKER, "quantity": 0.0001, "timeValidity": "DAY",
    })
    try:
        body = r.json()
    except Exception:
        body = {}
    err_type = body.get("type", "")
    is_regression_safe = r.status_code == 400 and err_type.endswith("invalid-request")
    _record(
        "market REJECTS timeValidity (anti-regression)",
        is_regression_safe,
        f"HTTP {r.status_code} type={err_type}",
    )
    if r.status_code == 200:
        # T212 mudou comportamento — começou a aceitar timeValidity → cancelar e flag
        _cancel_orders_for(SELL_TEST_TICKER)


def test_market_sell_schema(position):
    """Confirma que MARKET aceita quantity NEGATIVA como SELL."""
    print("\n[5] POST /equity/orders/market — schema SELL (quantity negativa)")
    if position is None:
        _record("sell test", False, "skipped: carteira vazia")
        return

    ticker = position["ticker"]
    # quantity negativa minúscula — abaixo do mínimo, prova schema sem fillar
    r = _post("/equity/orders/market", {"ticker": ticker, "quantity": -0.0001})
    ok, err_type = _is_schema_accepted(r)
    try:
        side_in_response = r.json().get("side") if r.status_code == 200 else None
    except Exception:
        side_in_response = None
    _record(
        "market accepts negative quantity (= SELL)",
        ok,
        f"HTTP {r.status_code} type={err_type} side={side_in_response}",
    )
    if r.status_code == 200:
        _cancel_orders_for(ticker)


def test_limit_schema():
    """Confirma que LIMIT aceita timeValidity:'DAY' (diferente do MARKET)."""
    print("\n[6] POST /equity/orders/limit — schema (com timeValidity)")
    payload = {"ticker": LIMIT_TEST_TICKER, "quantity": 1, "limitPrice": 1.00, "timeValidity": "DAY"}
    r = _post("/equity/orders/limit", payload)
    ok, err_type = _is_schema_accepted(r)
    _record(
        "limit accepts timeValidity:'DAY'",
        ok,
        f"HTTP {r.status_code} type={err_type}",
    )
    if r.status_code == 200:
        _cancel_orders_for(LIMIT_TEST_TICKER)


def test_nonexistent_sell_endpoints():
    """Confirma que os endpoints "SELL alternativos" continuam a NÃO existir."""
    print("\n[7] Anti-regression — endpoints DELETE não existem")

    r = _delete(f"/equity/positions/{SELL_TEST_TICKER}")
    _record(
        "DELETE /equity/positions/{ticker} → 404",
        r.status_code == 404,
        f"HTTP {r.status_code}",
    )

    r = _delete(f"/equity/portfolio/{SELL_TEST_TICKER}")
    _record(
        "DELETE /equity/portfolio/{ticker} → 405",
        r.status_code == 405,
        f"HTTP {r.status_code}",
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _select_env(env: str, allow_live: bool) -> int | None:
    """Configura BASE_URL e auth do _session segundo --env. Devolve exit code se falhar."""
    global BASE_URL, ENV_LABEL

    if env == "demo":
        if not T212_DEMO_KEY or T212_DEMO_KEY == "Basic ":
            print("ERRO: T212_DEMO_KEY não configurada (verifica T212_API_ID + T212_API_KEY no .env)")
            return 2
        BASE_URL = T212_BASE_URL_DEMO
        ENV_LABEL = "demo"
        _session.headers["Authorization"] = T212_DEMO_KEY
        return None

    # env == "live"
    if not allow_live:
        print("ERRO: --env live requer --i-understand-risk para confirmar intenção.")
        print()
        print("  Live mode envia chamadas autenticadas para live.trading212.com — a tua conta real.")
        print("  Os testes evitam ordens fillable (qty abaixo do mínimo + LIMIT a $1 + tickers")
        print("  com schema-fail), mas a responsabilidade de garantir que o .env aponta para a")
        print("  conta certa é tua. Repete com:")
        print("    python scripts/t212_contract_test.py --env live --i-understand-risk")
        return 2

    if not T212_LIVE_KEY:
        print("ERRO: T212_LIVE_API_ID / T212_LIVE_API_KEY não configurados no .env.")
        print("  Gera credenciais separadas em T212 → Settings → API (conta live)")
        print("  e adiciona ao .env:")
        print("    T212_LIVE_API_ID=...")
        print("    T212_LIVE_API_KEY=...")
        return 2

    BASE_URL = T212_BASE_URL_LIVE
    ENV_LABEL = "live"
    _session.headers["Authorization"] = T212_LIVE_KEY
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="T212 API contract test")
    parser.add_argument(
        "--env", choices=["demo", "live"], default="demo",
        help="ambiente alvo (default: demo)",
    )
    parser.add_argument(
        "--i-understand-risk", action="store_true",
        help="confirmação obrigatória para --env live",
    )
    args = parser.parse_args()

    err = _select_env(args.env, allow_live=args.i_understand_risk)
    if err is not None:
        return err

    banner_warn = " ⚠️  LIVE MODE  ⚠️ " if ENV_LABEL == "live" else ""
    print(f"T212 contract test [{ENV_LABEL}]{banner_warn} → {BASE_URL}")

    position = test_auth_and_portfolio()
    test_cash()
    test_orders_list()
    test_market_buy_schema()
    test_market_sell_schema(position)
    test_limit_schema()
    test_nonexistent_sell_endpoints()

    # Limpeza defensiva final — críticO em live (qualquer ordem residual é dinheiro real)
    if position:
        _cancel_orders_for(position["ticker"])
    _cancel_orders_for(LIMIT_TEST_TICKER)

    n_total  = len(results)
    n_passed = sum(1 for _, ok, _ in results if ok)
    n_failed = n_total - n_passed

    print(f"\n{'='*60}")
    print(f"Resultado [{ENV_LABEL}]: {n_passed}/{n_total} passes ({n_failed} regressões)")
    if n_failed:
        print("\nRegressões detectadas:")
        for name, ok, detail in results:
            if not ok:
                print(f"  ✗ {name} — {detail}")
        if ENV_LABEL == "live":
            print(
                "\n→ LIVE difere de DEMO. NÃO fazer flip de LIVE_TRADING — actualizar primeiro:\n"
                "  - docs/T212_API_MANUAL.md (secção §11 Demo vs Live)\n"
                "  - bot/api_client.py se algum schema requer adaptação"
            )
        else:
            print("\n→ T212 mudou comportamento. Actualizar docs/T212_API_MANUAL.md e api_client.py.")
        return 1
    print("Todas as premissas continuam válidas — manual + api_client coerentes com a API real.")
    if ENV_LABEL == "live":
        print(
            "\n→ Paridade demo/live confirmada para os 13 asserts cobertos. Flip de LIVE_TRADING\n"
            "  pode prosseguir se os restantes pré-requisitos da Fase 3 estiverem satisfeitos\n"
            "  (ver vault/specs/FASE-1.md §9.4)."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
