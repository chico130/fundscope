"""
wipe_demo.py — Limpa o estado local do FundScope após reset da conta demo T212.

Uso:
    python scripts/wipe_demo.py           # pede confirmação
    python scripts/wipe_demo.py --force   # sem confirmação (para scripts)

O que faz:
  1. Arquiva os ficheiros de estado actuais em data/wipe_archive/<timestamp>/
  2. Repõe positions_ledger, portfolio, diario_trades e throttler ao estado limpo
  3. Preserva: thresholds Bonnie, regime, watchlist, params de backtest, logs históricos

Nota: NÃO apaga o modelo Bonnie nem os parâmetros de trading.
Lembra-te de actualizar o API key T212 no .env se a conta foi resetada.
"""

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARCHIVE_DIR = ROOT / "data" / "wipe_archive"

# Ficheiros a REPOR ao estado limpo (conteúdo default após wipe)
WIPE_TARGETS: dict[str, object] = {
    "data/beta/positions_ledger.json": {
        "last_updated": None,
        "last_t212_sync": None,
        "cash_eur": 0.0,
        "positions": {},
    },
    "data/beta/beta_trades.json": [],
    "data/beta/beta_analysis.json": {
        "period": None,
        "n_trades": 0,
        "win_rate_pct": 0.0,
        "avg_gain_pct": 0.0,
        "avg_loss_pct": 0.0,
        "sharpe_ratio": None,
        "max_drawdown_pct": 0.0,
        "total_return_pct": 0.0,
        "timestamp": None,
    },
    "data/beta/beta_equity.json": [],
    "data/beta/beta_positions.json": [],
    "data/beta/beta_summary.json": {
        "total_value": 0.0,
        "total_invested": 0.0,
        "total_gain_eur": 0.0,
        "n_positions": 0,
        "cash_eur": 0.0,
    },
    "data/beta/position_meta.json": {},
    "data/beta/throttler_state.json": {
        "buy_timestamps": [],
        "last_reset": None,
    },
    "data/beta/cro_insights.json": {
        "last_updated": None,
        "insights": [],
        "risk_factor": 1.0,
        "win_rate_7d": None,
        "drawdown_pct": None,
    },
    "diario_trades.json": [],
    "portfolio.json": {
        "updated": None,
        "t212_mode": "demo",
        "summary": {
            "total_value": 0.0,
            "total_invested": 0.0,
            "total_gain_eur": 0.0,
            "total_gain_pct": 0.0,
            "daily_gain_eur": 0.0,
            "cash_available": 0.0,
            "n_positions": 0,
        },
        "positions": [],
        "benchmark_metrics": {},
    },
}

# Ficheiros a NÃO tocar (configuração + modelos + dados de mercado)
PRESERVE = {
    "data/beta/bonnie_thresholds*.json",
    "data/beta/regime.json",
    "data/beta/watchlist.json",
    "data/beta/watchlist_fundamentals.json",
    "data/beta/optimized_backtest_params.json",
    "data/beta/earnings_ai.json",
    "data/beta/social_sentiment.json",
    "data/beta/last_wake.txt",
}


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def wipe(force: bool = False) -> None:
    print("=" * 60)
    print(" FUNDSCOPE — WIPE ESTADO DEMO")
    print("=" * 60)
    print()
    print("Esta operação vai:")
    print("  • Arquivar estado actual em data/wipe_archive/<timestamp>/")
    print("  • Repor posições, trades e diário ao estado limpo")
    print("  • Preservar modelos Bonnie, watchlist e parâmetros")
    print()
    print("NÃO ESQUECER após o wipe:")
    print("  • Verificar/actualizar T212_API_KEY no .env se a conta foi resetada")
    print()

    if not force:
        resp = input("Confirmas o wipe? (s/N): ").strip().lower()
        if resp not in ("s", "sim", "y", "yes"):
            print("Cancelado.")
            return

    ts = _ts()
    archive = ARCHIVE_DIR / ts
    archive.mkdir(parents=True, exist_ok=True)
    print(f"\nArchivo → {archive}")

    # 1. Arquivar estado actual
    archived = 0
    for rel in WIPE_TARGETS:
        src = ROOT / rel
        if src.exists():
            dst = archive / Path(rel).name
            shutil.copy2(src, dst)
            archived += 1
    print(f"  {archived} ficheiros arquivados")

    # 2. Repor ao estado limpo
    now_iso = datetime.now(timezone.utc).isoformat()
    wiped = 0
    for rel, default in WIPE_TARGETS.items():
        target = ROOT / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        # Injectar timestamp no estado limpo onde aplicável
        if isinstance(default, dict) and "last_updated" in default:
            default = {**default, "last_updated": now_iso}
        target.write_text(json.dumps(default, indent=2, ensure_ascii=False), encoding="utf-8")
        wiped += 1

    print(f"  {wiped} ficheiros repostos ao estado limpo")
    print()
    print("✅ Wipe concluído. Sistema pronto para nova sessão demo.")
    print(f"   Arquivo guardado em: data/wipe_archive/{ts}/")
    print()
    print("Próximos passos:")
    print("  1. Verificar/actualizar T212_API_KEY no .env")
    print("  2. Reiniciar o bot: systemctl restart fundscope (ou pm2 restart)")
    print("  3. Correr update_portfolio.py para sincronizar com a nova conta")


if __name__ == "__main__":
    force = "--force" in sys.argv
    wipe(force=force)
