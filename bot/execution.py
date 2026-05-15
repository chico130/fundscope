"""
Execution module — Fase 1.

Wraps api_client order placement with:
  - leitura de config_risco.json antes de qualquer BUY
  - bloqueio por Bonnie (permite_comprar == false)
  - aplicação do fator tamanho_maximo_posicao ao volume
  - log duplo: diario_trades.json (raiz) + data/beta/beta_trades.json
  - pre-flight risk check via strategy.check_risk_limits

LIVE_TRADING must be False; api_client enforces this but we double-check here.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import api_client
from .config import (
    DATA_BETA_DIR,
    DIARIO_TRADES_PATH,
    CONFIG_RISCO_PATH,
    LIVE_TRADING,
    STRATEGY_VERSION,
)
from .logger import log_decision, log_error, log_trade
from .strategy import ProposedTrade, check_risk_limits

_DEFAULT_CONFIG_RISCO: dict = {
    "permite_comprar": True,
    "tamanho_maximo_posicao": 1.0,
    "motivo_bloqueio": "",
    "estado_emocional": "neutro",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_config_risco() -> dict:
    """Lê config_risco.json da raiz. Devolve defaults conservadores em caso de erro."""
    if not CONFIG_RISCO_PATH.exists():
        return dict(_DEFAULT_CONFIG_RISCO)
    try:
        with open(CONFIG_RISCO_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return dict(_DEFAULT_CONFIG_RISCO)
        return {**_DEFAULT_CONFIG_RISCO, **data}
    except (json.JSONDecodeError, OSError) as exc:
        log_error("config_risco_read_error", {"error": str(exc)})
        return dict(_DEFAULT_CONFIG_RISCO)


def _append_to_diario_trades(entry: dict) -> None:
    """Appends entry to diario_trades.json na raiz do projecto (array JSON)."""
    DIARIO_TRADES_PATH.parent.mkdir(parents=True, exist_ok=True)
    records: list = []
    if DIARIO_TRADES_PATH.exists():
        try:
            with open(DIARIO_TRADES_PATH, "r", encoding="utf-8") as f:
                records = json.load(f)
            if not isinstance(records, list):
                records = []
        except (json.JSONDecodeError, OSError):
            records = []
    records.append(entry)
    tmp = DIARIO_TRADES_PATH.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=2, ensure_ascii=False)
        tmp.replace(DIARIO_TRADES_PATH)
    except OSError as exc:
        log_error("diario_trades_write_error", {"error": str(exc)})
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _fill_price(response: dict) -> float | None:
    if not isinstance(response, dict):
        return None
    return response.get("fillPrice") or response.get("limitPrice") or response.get("price")


def _append_to_beta_trades(trade_record: dict) -> None:
    """Appends a new trade to data/beta/beta_trades.json (atomic write)."""
    path = DATA_BETA_DIR / "beta_trades.json"
    path.parent.mkdir(parents=True, exist_ok=True)

    data: dict = {"trades": []}
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            data = {"trades": []}

    data["trades"].append(trade_record)

    tmp = path.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        tmp.replace(path)
    except OSError as exc:
        log_error("beta_trades_write_error", {"error": str(exc)})
        if tmp.exists():
            tmp.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def execute_trade(proposed: ProposedTrade, portfolio_state: dict) -> dict | None:
    """Executa uma ordem proposta após validação de risco e verificação Bonnie.

    Para ordens BUY:
      1. Lê config_risco.json — se permite_comprar == False, regista bloqueio
         em diario_trades.json e devolve None.
      2. Multiplica qty por tamanho_maximo_posicao.

    Returns the trade record dict on success, None on failure or risk block.
    Side-effects: writes to daily log + diario_trades.json + beta_trades.json.
    """
    if LIVE_TRADING:
        raise RuntimeError("LIVE_TRADING é True — abortar para proteger a conta real.")

    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    rsi_now: float | None = proposed.context.get("rsi_14") if proposed.context else None

    # ── Bonnie gate (apenas para BUY) ─────────────────────────────────────
    if proposed.side.upper() == "BUY":
        cfg = _read_config_risco()
        if not cfg.get("permite_comprar", True):
            motivo = cfg.get("motivo_bloqueio", "bloqueio_bonnie")
            _append_to_diario_trades({
                "tipo": "bloqueado",
                "bloqueado_por": "bonnie",
                "motivo": motivo,
                "ativo": proposed.ticker,
                "timestamp": ts,
            })
            log_decision("bonnie_block", "skip_buy", {
                "ticker": proposed.ticker,
                "motivo": motivo,
            })
            return None

        # Aplica fator de tamanho
        fator = float(cfg.get("tamanho_maximo_posicao", 1.0))
        if fator != 1.0:
            proposed = ProposedTrade(
                ticker=proposed.ticker,
                side=proposed.side,
                qty=round(proposed.qty * fator, 4),
                order_type=proposed.order_type,
                price=proposed.price,
                reason=proposed.reason,
                context=proposed.context,
                signal_strength=proposed.signal_strength,
            )

    # ── Risk check (strategy layer) ────────────────────────────────────────
    if not check_risk_limits(proposed, portfolio_state):
        return None

    trade_id = f"{ts}_{proposed.ticker}_{proposed.side}"

    log_decision("pre_execution", "place_order", {
        "id": trade_id,
        "ticker": proposed.ticker,
        "side": proposed.side,
        "qty": proposed.qty,
        "type": proposed.order_type,
    })

    response = api_client.place_order_demo(
        ticker=proposed.ticker,
        side=proposed.side,
        qty=proposed.qty,
        order_type=proposed.order_type,
        price=proposed.price,
    )

    if response is None:
        log_error("execution_failed", {
            "id": trade_id,
            "ticker": proposed.ticker,
            "side": proposed.side,
        })
        return None

    fill_price = proposed.price or _fill_price(response)

    trade_record: dict = {
        "id": trade_id,
        "datetime": now.isoformat(),
        "ticker": proposed.ticker,
        "side": proposed.side,
        "qty": proposed.qty,
        "price": fill_price,
        "env": "demo",
        "strategy_version": STRATEGY_VERSION,
        "reason": proposed.reason,
        "context": proposed.context,
        "result_eur": None,
        "result_pct": None,
        "result_after_minutes": 1440,
        "closed_at": None,
        "postmortem": None,
    }

    # ── Diário público (raiz) ──────────────────────────────────────────────
    _append_to_diario_trades({
        "tipo": "entrada" if proposed.side.upper() == "BUY" else "saida",
        "ativo": proposed.ticker,
        "lado": proposed.side.upper(),
        "volume": proposed.qty,
        "preco": fill_price,
        "resultado_eur": None,
        "rsi": rsi_now,
        "timestamp": ts,
    })

    log_trade(trade_record)
    _append_to_beta_trades(trade_record)
    return trade_record


def execute_exit(ticker: str, position: dict, reason: str, rsi: float | None = None) -> dict | None:
    """Fecha uma posição inteira com LIMIT 0.3% abaixo do preço actual."""
    qty = position.get("quantity", 0)
    if qty <= 0:
        return None

    current_price = position.get("current_price")
    if current_price and current_price > 0:
        limit_price = round(float(current_price) * 0.997, 4)
        order_type = "LIMIT"
    else:
        limit_price = None
        order_type = "MARKET"

    ctx = {}
    if rsi is not None:
        ctx["rsi_14"] = rsi

    proposed = ProposedTrade(
        ticker=ticker,
        side="SELL",
        qty=qty,
        order_type=order_type,
        price=limit_price,
        reason=reason,
        context=ctx,
        signal_strength=1.0,
    )
    return execute_trade(proposed, {"positions": [position], "cash": {"free": 0}})
