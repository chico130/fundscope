"""
test_ordem_demo.py — teste de compra na conta demo T212.

Coloca uma ordem LIMIT bem abaixo do mercado para que fique em fila
sem ser executada. Cancela automaticamente no final se corrrer com --cancelar.

Uso:
    python test_ordem_demo.py              # coloca a ordem e mostra resultado
    python test_ordem_demo.py --cancelar   # cancela a última ordem de teste
"""
import sys, json, argparse

sys.stdout.reconfigure(encoding="utf-8")

from bot.config import T212_DEMO_KEY, T212_BASE_URL_DEMO, DATA_BETA_DIR, RISK_CONFIG
from bot.data_layer import get_full_portfolio_state
from bot.cro import CRO
from bot import api_client
import requests

# ── Parâmetros do teste ───────────────────────────────────────────────────────
TICKER      = "GOOGL_US_EQ"   # GOOGL — já está na conta demo
QTY         = 0.02            # ~$8 ao preço actual (~$395) — simbólico
LIMIT_PRICE = 340.0           # $55 abaixo do mercado → fica em fila garantidamente
# ─────────────────────────────────────────────────────────────────────────────

SEP = "=" * 55

session = requests.Session()
session.headers.update({
    "Authorization": T212_DEMO_KEY,
    "Content-Type":  "application/json",
})


def colocar_ordem():
    print(f"\n{SEP}")
    print("TESTE DE COMPRA — CONTA DEMO T212")
    print(f"{SEP}\n")

    # ── 1. Estado actual ──────────────────────────────────────────────────────
    print("1. ESTADO DO PORTFOLIO")
    print("   A sincronizar com T212...")
    state    = get_full_portfolio_state()
    cash     = state.get("cash", {})
    free     = cash.get("free", 0)
    total    = cash.get("total", 0)
    invested = cash.get("invested", 0)
    positions = state.get("positions", [])
    print(f"   Cash livre:   €{free:,.2f}")
    print(f"   Investido:    €{invested:,.2f}")
    print(f"   Total conta:  €{total:,.2f}")
    print(f"   Posições:     {len(positions)}")
    for p in positions:
        sym  = p.get("ticker", "?").split("_")[0]
        qty  = p.get("quantity", 0)
        curr = p.get("currentPrice", 0)
        ppl  = p.get("ppl", 0)
        print(f"     · {sym}  qty={qty:.5f}  preço=${curr:.2f}  P&L={ppl:+.2f}€")

    # ── 2. CRO — Factor de risco dinâmico ────────────────────────────────────
    print(f"\n2. CRO — FACTOR DE RISCO DINÂMICO")
    cro     = CRO()
    cro.observe(DATA_BETA_DIR / "beta_trades.json", state)
    verdict = cro.interpret(state, regime="bull_lateral")

    print(f"   Risk factor:  {verdict.risk_factor:.2f}×")
    print(f"   Win rate 7d:  {verdict.win_rate_7d*100:.1f}%")
    print(f"   Drawdown:     {verdict.drawdown_pct:.2f}%")
    print(f"   Aprovado CRO: {'SIM' if verdict.approved else 'NAO'}")
    print("   Insights:")
    for ins in verdict.insights:
        print(f"     · {ins}")

    # ── 3. Bonnie — Simulação da auditoria ───────────────────────────────────
    print(f"\n3. BONNIE — SIMULACAO DE AUDITORIA")
    equity = free + invested
    order_value_eur = QTY * LIMIT_PRICE / 1.12   # USD→EUR (approx)
    pct_carteira    = order_value_eur / equity * 100 if equity else 0
    max_pct         = RISK_CONFIG["max_position_pct"]
    bonnie_ok       = pct_carteira <= max_pct and verdict.approved

    print(f"   Ordem:        BUY {QTY} × {TICKER} @ ${LIMIT_PRICE:.2f}")
    print(f"   Valor aprox:  €{order_value_eur:.2f} ({pct_carteira:.1f}% da carteira)")
    print(f"   Limite máx:   {max_pct}% por posição")
    print(f"   Win rate ok:  {'SIM' if verdict.win_rate_7d >= 0.40 else 'NAO (abaixo de 40%)'}")
    print(f"   Dimensão ok:  {'SIM' if pct_carteira <= max_pct else 'NAO'}")
    print(f"   BONNIE:       {'APROVARIA ✓' if bonnie_ok else 'VETARIA ✗'}")

    # ── 4. Colocar ordem no T212 Demo ─────────────────────────────────────────
    print(f"\n4. T212 DEMO — ENVIAR ORDEM LIMIT")
    print(f"   Ticker:       {TICKER}")
    print(f"   Quantidade:   {QTY}")
    print(f"   Preço limite: ${LIMIT_PRICE}  (mercado ~$395 → fica em fila)")
    print(f"   Validade:     DAY")
    print("   A enviar...")

    resp = session.post(
        f"{T212_BASE_URL_DEMO}/equity/orders/limit",
        json={
            "ticker":       TICKER,
            "quantity":     QTY,
            "limitPrice":   LIMIT_PRICE,
            "timeValidity": "DAY",
        },
        timeout=30,
    )

    print(f"\n   HTTP {resp.status_code}")
    data = resp.json()
    print("   Resposta T212:")
    print("   " + json.dumps(data, indent=4, ensure_ascii=False).replace("\n", "\n   "))

    if resp.status_code == 200 and isinstance(data, dict):
        order_id = data.get("id")
        status   = data.get("status", "?")
        print(f"\n   ID da ordem:  {order_id}")
        print(f"   Estado:       {status}")
        if order_id:
            print(f"\n   Para cancelar: python test_ordem_demo.py --cancelar --id {order_id}")

    print(f"\n{SEP}")
    print("NOTA: Mercado fechado → ordem fica em fila até ao próximo open.")
    print("Podes cancelar na app T212 ou com --cancelar --id <ID>.")
    print(f"{SEP}\n")

    return data


def cancelar_ordem(order_id: str):
    print(f"\nA cancelar ordem {order_id}...")
    ok = api_client.cancel_order_demo(order_id)
    if ok:
        print(f"Ordem {order_id} cancelada com sucesso.")
    else:
        resp = session.delete(f"{T212_BASE_URL_DEMO}/equity/orders/{order_id}", timeout=15)
        print(f"HTTP {resp.status_code}: {resp.text}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cancelar", action="store_true")
    parser.add_argument("--id", type=str, default=None)
    args = parser.parse_args()

    if args.cancelar:
        if not args.id:
            print("Usa --id <ORDER_ID> para cancelar.")
        else:
            cancelar_ordem(args.id)
    else:
        colocar_ordem()
