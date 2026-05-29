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

from . import api_client
from .config import (
    DATA_BETA_DIR,
    DIARIO_TRADES_PATH,
    CONFIG_RISCO_PATH,
    LIVE_TRADING,
    STRATEGY_VERSION,
)
from .logger import log_decision, log_error, log_trade
from .notifier import enviar_alerta
from .strategy import ProposedTrade

_DEFAULT_CONFIG_RISCO: dict = {
    "permite_comprar": True,
    "tamanho_maximo_posicao": 1.0,
    "motivo_bloqueio": "",
    "estado_emocional": "neutro",
}

_PENDING_TRADES_PATH = DATA_BETA_DIR.parent / "pending_trades.json"
_PENDING_MAX_RETRIES = 3


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


def _mark_open_buys_closed(ticker: str, closed_at: str) -> int:
    """Sets closed_at on all open BUY trades for ticker. Returns number of records updated."""
    path = DATA_BETA_DIR / "beta_trades.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return 0
    updated = 0
    for trade in data.get("trades", []):
        if (
            trade.get("ticker") == ticker
            and trade.get("side", "").upper() == "BUY"
            and not trade.get("closed_at")
        ):
            trade["closed_at"] = closed_at
            updated += 1
    if updated:
        tmp = path.with_name(path.name + ".tmp")
        try:
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            log_error("mark_buys_closed_write_error", {"ticker": ticker, "error": str(exc)})
            return 0
    return updated


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
# Pending trades queue — BUY orders that failed due to T212 API errors
# are persisted here and retried on the next cycle.
# ---------------------------------------------------------------------------

def _queue_pending_trade(proposed: ProposedTrade) -> None:
    """Append a failed BUY intent to data/pending_trades.json for next-cycle retry."""
    try:
        records: list = []
        if _PENDING_TRADES_PATH.exists():
            try:
                records = json.loads(_PENDING_TRADES_PATH.read_text(encoding="utf-8"))
                if not isinstance(records, list):
                    records = []
            except (json.JSONDecodeError, OSError):
                records = []
        records.append({
            "queued_at":    datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ticker":       proposed.ticker,
            "side":         proposed.side,
            "qty":          proposed.qty,
            "order_type":   proposed.order_type,
            "price":        proposed.price,
            "reason":       proposed.reason,
            "context":      proposed.context,
            "signal_strength": proposed.signal_strength,
            "retry_count":  0,
        })
        _PENDING_TRADES_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _PENDING_TRADES_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_PENDING_TRADES_PATH)
    except Exception as exc:
        log_error("pending_trades_write_error", {"ticker": proposed.ticker, "error": str(exc)})


def flush_pending_trades(portfolio_state: dict) -> list[dict]:
    """Re-attempt queued BUY trades.  Called at the start of each cycle.

    Returns list of successfully executed trade records.
    Removes executed and exhausted entries from the queue.
    Increments retry_count and drops entries that exceed _PENDING_MAX_RETRIES.
    """
    if not _PENDING_TRADES_PATH.exists():
        return []
    try:
        records = json.loads(_PENDING_TRADES_PATH.read_text(encoding="utf-8"))
        if not isinstance(records, list) or not records:
            return []
    except (json.JSONDecodeError, OSError):
        return []

    executed: list[dict] = []
    remaining: list[dict] = []

    # Build a ticker→quantity map from the live portfolio for SELL qty resolution.
    live_positions: dict[str, float] = {
        p["ticker"]: float(p.get("quantity") or 0)
        for p in (portfolio_state.get("positions") or [])
        if p.get("ticker") and float(p.get("quantity") or 0) > 0
    }

    for rec in records:
        retry_count = rec.get("retry_count", 0)
        ticker      = rec.get("ticker", "")
        side        = rec.get("side", "BUY").upper()

        if retry_count >= _PENDING_MAX_RETRIES:
            log_error("pending_trade_expired", {
                "ticker":    ticker,
                "side":      side,
                "queued_at": rec.get("queued_at"),
                "retries":   retry_count,
            })
            continue

        # For SELL retries use the live position quantity instead of the
        # stored qty — the position size may have changed since queuing.
        if side == "SELL":
            live_qty = live_positions.get(ticker)
            if not live_qty:
                log_decision("pending_sell_skipped", "position_gone", {"ticker": ticker})
                continue  # position already closed; discard entry
            qty = live_qty
        else:
            qty = rec.get("qty", 0)

        proposed = ProposedTrade(
            ticker=ticker,
            side=side,
            qty=qty,
            order_type=rec.get("order_type", "MARKET"),
            price=rec.get("price"),
            reason=f"[retry {retry_count + 1}/{_PENDING_MAX_RETRIES}] {rec.get('reason', '')}",
            context=rec.get("context"),
            signal_strength=rec.get("signal_strength", 1.0),
        )
        result = execute_trade(proposed, portfolio_state)
        if result:
            executed.append(result)
            print(
                f"[PENDING] {proposed.ticker}: retry {retry_count + 1} OK — ordem enviada.",
                flush=True,
            )
        else:
            rec["retry_count"] = retry_count + 1
            remaining.append(rec)

    try:
        tmp = _PENDING_TRADES_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(remaining, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_PENDING_TRADES_PATH)
    except Exception as exc:
        log_error("pending_trades_write_error", {"error": str(exc)})

    return executed


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
        preco_str = f"${proposed.price:.2f}" if proposed.price else "N/D"
        _buy_reason_str = proposed.reason or "sinal técnico"
        enviar_alerta(
            f"[CLYDE] 📈 Sinal de COMPRA detetado em {proposed.ticker}!"
            f" Preço: {preco_str}.\n"
            f"Motivo: {_buy_reason_str}\n"
            f"A aguardar auditoria da Bonnie..."
        )
        cfg = _read_config_risco()
        if not cfg.get("permite_comprar", True):
            motivo = cfg.get("motivo_bloqueio", "bloqueio_bonnie")
            ctx = proposed.context or {}
            contexto: dict = {}
            if rsi_now is not None:
                contexto["rsi_14"] = rsi_now
            if ctx.get("regime"):
                contexto["regime"] = ctx["regime"]
            _append_to_diario_trades({
                "tipo": "bloqueado",
                "bloqueado_por": "bonnie",
                "motivo": motivo,
                "ativo": proposed.ticker,
                "timestamp": ts,
                "reason": proposed.reason,
                "contexto": contexto,
            })
            log_decision("bonnie_block", "skip_buy", {
                "ticker": proposed.ticker,
                "motivo": motivo,
            })
            print(
                f"[EXECUTE BLOCK] {proposed.ticker}: config_risco.permite_comprar=False "
                f"(motivo={motivo})",
                flush=True,
            )
            enviar_alerta(
                f"[BONNIE VETO] 🚨 Compra de {proposed.ticker} BLOQUEADA!"
                f" Motivo: {motivo}"
                f" (probabilidade abaixo do threshold ou Bear regime)."
            )
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

    # ── Basic size check ──────────────────────────────────────────────────────
    # Nota: o phase0 já garante max_position_pct com conversão EUR/USD correcta.
    # O check de posição aqui era redundante e misturava EUR (cash) com USD
    # (currentPrice de acções US), fazendo o rácio inflacionar ~10% e bloquear
    # ordens válidas silenciosamente.
    if proposed.qty <= 0:
        log_error("execution_zero_qty", {"ticker": proposed.ticker})
        print(
            f"[EXECUTE BLOCK] {proposed.ticker}: qty={proposed.qty} <= 0 — ordem ignorada",
            flush=True,
        )
        return None

    trade_id = f"{ts}_{proposed.ticker}_{proposed.side}"

    log_decision("pre_execution", "place_order", {
        "id": trade_id,
        "ticker": proposed.ticker,
        "side": proposed.side,
        "qty": proposed.qty,
        "type": proposed.order_type,
    })

    if proposed.side.upper() == "SELL":
        # T212 rejeita SELL market com quantidade negativa e não suporta frações
        # em limit orders. Usa DELETE /equity/positions/{ticker} para fechar tudo.
        ok = api_client.close_position_demo(proposed.ticker, proposed.qty)
        response = {"closed": True} if ok else None
    else:
        response = api_client.place_order_demo(
            ticker=proposed.ticker,
            side=proposed.side,
            qty=proposed.qty,
            order_type=proposed.order_type,
            price=proposed.price,
        )

    if response is None:
        order_err = api_client.get_last_order_error()
        t212_code = (order_err or {}).get("t212_code", "unknown")
        t212_msg  = (order_err or {}).get("t212_message", "")
        http_code = (order_err or {}).get("status_code", "?")
        err_detail = f"HTTP {http_code} — {t212_code}"
        if t212_msg:
            err_detail += f": {t212_msg}"

        log_error("execution_failed", {
            "id":         trade_id,
            "ticker":     proposed.ticker,
            "side":       proposed.side,
            "qty":        proposed.qty,
            "price":      proposed.price,
            "order_type": proposed.order_type,
            "t212_code":  t212_code,
            "http_code":  http_code,
        })
        print(
            f"[EXECUTE BLOCK] {proposed.ticker}: T212 rejeitou ordem "
            f"({proposed.side} {proposed.qty} @ {proposed.price}, type={proposed.order_type})\n"
            f"  Motivo: {err_detail}",
            flush=True,
        )

        # Instrument unknown is a config error — retrying won't fix it
        if t212_code in ("InstrumentNotFound", "InvalidInstrument") or "nstrument" in t212_code:
            enviar_alerta(
                f"[CLYDE] ❌ Ticker inválido: {proposed.ticker}\n"
                f"A T212 não reconhece este instrumento. Verificar mapeamento de tickers.\n"
                f"Motivo: {err_detail}"
            )
            return None

        # Queue both BUY and SELL failures; flush_pending_trades() will
        # re-attempt them at the start of the next cycle.  For SELLs the flush
        # uses the current live position quantity to avoid qty drift.
        _queue_pending_trade(proposed)
        _price_str = f" · price=${proposed.price:.2f}" if proposed.price else ""
        if proposed.side.upper() == "BUY":
            enviar_alerta(
                f"[CLYDE] ⚠️ Ordem BUY {proposed.ticker} rejeitada pela T212 — intenção guardada.\n"
                f"Motivo: {err_detail}\n"
                f"qty={proposed.qty} · type={proposed.order_type}{_price_str}\n"
                f"Será retentada no próximo ciclo (máx. {_PENDING_MAX_RETRIES} tentativas)."
            )
        else:
            enviar_alerta(
                f"[CLYDE] ⚠️ Ordem SELL {proposed.ticker} rejeitada pela T212 — intenção guardada.\n"
                f"Motivo: {err_detail}\n"
                f"qty={proposed.qty} · type={proposed.order_type}\n"
                f"Será retentada no próximo ciclo (máx. {_PENDING_MAX_RETRIES} tentativas)."
            )
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
        # ATR barriers — populated for BUY orders when ATR is available at entry
        "atr_at_entry":      None,
        "stop_loss_price":   None,
        "atr_trigger_price": None,
        "atr_target_price":  None,
        "break_even_active": False,
    }

    if proposed.side.upper() == "BUY" and fill_price:
        atr = (proposed.context or {}).get("atr_14")
        if atr:
            trade_record["atr_at_entry"]      = round(atr, 4)
            trade_record["stop_loss_price"]   = round(fill_price - 1.5 * atr, 4)
            trade_record["atr_trigger_price"] = round(fill_price + 1.0 * atr, 4)
            trade_record["atr_target_price"]  = round(fill_price + 3.0 * atr, 4)

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

    if proposed.side.upper() == "BUY":
        _fill_str = f"${fill_price:.2f}" if fill_price else "N/D"
        enviar_alerta(
            f"[BONNIE APROVADO] ✅ Compra de {proposed.ticker} AUTORIZADA!"
            f" Fill: {_fill_str} · qty {proposed.qty}\n"
            f"Porquê comprar: {proposed.reason or 'sinal técnico'}"
        )
    else:
        # Mark the original open BUY record(s) as closed so exit_manager doesn't
        # re-propose the same exit on subsequent cycles.
        n_closed = _mark_open_buys_closed(proposed.ticker, ts)
        _fill_str = f"${fill_price:.2f}" if fill_price else "N/D"
        if n_closed:
            enviar_alerta(
                f"[CLYDE] ✅ Posição {proposed.ticker} FECHADA. Fill: {_fill_str}\n"
                f"Porquê vender: {proposed.reason or 'saída técnica'}"
            )
        else:
            # No open BUY found — position was already closed in a prior cycle.
            log_decision("sell_no_open_buy", "duplicate_close_suppressed", {
                "ticker": proposed.ticker,
                "fill":   _fill_str,
            })

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
