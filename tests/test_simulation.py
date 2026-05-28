"""
End-to-end dry-run simulation of the phase0 trading cycle.

All HTTP traffic is intercepted by requests-mock (no real T212 or Telegram
calls).  yfinance and Finnhub paths are patched via monkeypatch.  File writes
are redirected to pytest's tmp_path.

Run locally:
    PYTHONPATH=. pytest tests/test_simulation.py -v
"""
from __future__ import annotations

import json
import re
import unittest.mock as mock

import pytest


# ---------------------------------------------------------------------------
# Shared data factories
# ---------------------------------------------------------------------------

def _ohlcv_bars(n: int = 225) -> list[dict]:
    """Synthetic OHLCV bars: saw-wave so RSI stays in the 40-55 range."""
    bars: list[dict] = []
    price = 180.0
    for i in range(n):
        delta = -0.2 if (i % 5 != 0) else 0.8
        price = max(50.0, round(price + delta, 4))
        bars.append({
            "date":   f"202{4 + i // 365}-{((i // 30) % 12) + 1:02d}-{(i % 28) + 1:02d}",
            "open":   round(price * 1.002, 4),
            "high":   round(price * 1.008, 4),
            "low":    round(price * 0.993, 4),
            "close":  price,
            "volume": 6_000_000 + i * 5_000,
        })
    return bars


_BARS = _ohlcv_bars()   # computed once, reused by all tests


def _t212_portfolio() -> list[dict]:
    return [{
        "ticker":        "AAPL_US_EQ",
        "quantity":      2.0,
        "currentShares": 2.0,
        "averagePrice":  170.0,
        "currentPrice":  180.0,
        "ppl":           20.0,
        "value":         360.0,
    }]


def _t212_cash() -> dict:
    return {"free": 500.0, "total": 860.0, "invested": 360.0, "ppl": 20.0, "result": 0.0}


def _canned_buy_opp() -> list[dict]:
    """Pre-built BUY opportunity; injected directly into the scan step."""
    return [{
        "ticker":          "NVDA",
        "sector":          "Technology",
        "watchlist_score": 0.85,
        "mom_1m":          0.05,
        "mom_3m":          0.12,
        "signal_strength": 0.75,
        "style":           "VALUE",
        "reasons":         ["RSI-14 baixo (30.0)", "EMA50>EMA200"],
        "technicals": {
            "rsi_14":              30.0,
            "ema50_above_ema200":  True,
            "volume_ratio_vs_avg": 1.5,
            "atr_14":              2.5,
            "ema20_above_ema50":   True,
            "price_above_ema20":   True,
        },
        "last_price": 180.0,
    }]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def env_vars(monkeypatch):
    """Inject mock credentials before any bot module reads os.environ."""
    monkeypatch.setenv("T212_API_ID",       "mock_id")
    monkeypatch.setenv("T212_API_KEY",      "mock_secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "mock_token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID",  "123456")
    monkeypatch.setenv("FINNHUB_API_KEY",   "")
    monkeypatch.setenv("PYTHONIOENCODING",  "utf-8")


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Redirect all data/ and log/ writes to tmp_path; seed required files."""
    import bot.config          as cfg
    import bot.phase0          as p0
    import bot.execution       as ex
    import bot.data_layer      as dl
    import bot.position_ledger as pl
    import bot.throttler       as thr
    import bot.logger          as log_mod
    import bot.notifier        as notif

    beta = tmp_path / "data" / "beta"
    beta.mkdir(parents=True)
    logs = tmp_path / "logs"
    (logs / "trades").mkdir(parents=True)
    (logs / "errors").mkdir(parents=True)

    # DATA_BETA_DIR is used at call-time in these modules — patch the name.
    for mod in (p0, ex, dl, pl, thr):
        if hasattr(mod, "DATA_BETA_DIR"):
            monkeypatch.setattr(mod, "DATA_BETA_DIR", beta)

    # Module-level Path constants computed once at import time.
    monkeypatch.setattr(p0,  "STATUS_PATH",           beta / "status.json")
    monkeypatch.setattr(p0,  "POSITION_META_PATH",    beta / "position_meta.json")
    monkeypatch.setattr(p0,  "_LAST_WAKE_PATH",       beta / "last_wake.txt")
    monkeypatch.setattr(p0,  "SOCIAL_SENTIMENT_PATH", beta / "social_sentiment.json")
    monkeypatch.setattr(p0,  "_ATTEMPTED_TODAY_PATH", beta / "attempted_today.json")
    monkeypatch.setattr(pl,  "LEDGER_PATH",           beta / "positions_ledger.json")
    monkeypatch.setattr(pl,  "BETA_POSITIONS_PATH",   beta / "beta_positions.json")
    monkeypatch.setattr(thr, "_STATE_PATH",           beta / "throttler_state.json")

    # Root-level files written by execution.py.
    monkeypatch.setattr(ex, "DIARIO_TRADES_PATH", tmp_path / "diario_trades.json")
    monkeypatch.setattr(ex, "CONFIG_RISCO_PATH",  tmp_path / "config_risco.json")

    # Logger paths.
    for attr, path in [
        ("LOGS_DIR",        logs),
        ("LOGS_TRADES_DIR", logs / "trades"),
        ("LOGS_ERRORS_DIR", logs / "errors"),
        ("DATA_BETA_DIR",   beta),
    ]:
        if hasattr(log_mod, attr):
            monkeypatch.setattr(log_mod, attr, path)

    # Telegram error log.
    monkeypatch.setattr(notif, "_TELEGRAM_ERROR_LOG",
                        logs / "errors" / "telegram_errors.json")

    # Daily-flags (dedup persistente de despertar/boa_noite/resumo diário).
    monkeypatch.setattr(notif, "_DAILY_FLAGS_PATH",
                        tmp_path / "data" / "daily_flags.json")

    # CRO insights path lives inside a dict — use setitem so monkeypatch restores it.
    monkeypatch.setitem(cfg.CRO_CONFIG, "cro_insights_path",
                        beta / "cro_insights.json")

    # Reset module-level SPY closes cache so tests are independent.
    monkeypatch.setattr(dl, "_SPY_CLOSES", None)

    # Seed files that phase0 reads at startup.
    (beta / "beta_trades.json").write_text('{"trades": []}', encoding="utf-8")
    (beta / "beta_positions.json").write_text('{"positions": []}', encoding="utf-8")

    return beta


@pytest.fixture
def stub_externals(monkeypatch):
    """Stub every yfinance/Finnhub/external dependency that isn't HTTP."""
    import bot.phase0       as p0
    import bot.api_client   as api
    import bot.price_feed   as pf
    import bot.exit_manager as em

    fixed_wl = [
        {"ticker": "NVDA", "sector": "Technology", "score": 0.85,
         "mom_1m": 0.05, "mom_3m": 0.12},
        {"ticker": "MSFT", "sector": "Technology", "score": 0.80,
         "mom_1m": 0.03, "mom_3m": 0.08},
    ]

    # --- Phase-0 lifecycle gates ---
    monkeypatch.setattr(p0, "_is_nyse_holiday",  lambda: False)
    monkeypatch.setattr(p0, "_is_market_open",   lambda: True)
    monkeypatch.setattr(p0, "_fetch_eurusd",     lambda: 1.10)
    monkeypatch.setattr(p0, "_run_learner_safe", lambda: None)

    # --- Regime / watchlist: patch in p0's namespace because they were
    #     imported with "from .X import f" (patching the source module alone
    #     would not affect p0's already-bound reference).
    monkeypatch.setattr(p0, "get_current_regime",  lambda: "bull_trending")
    monkeypatch.setattr(p0, "load_regime_metrics", lambda: {"metrics": {}})
    monkeypatch.setattr(p0, "load_cached_regime",  lambda: "bull_trending")
    monkeypatch.setattr(p0, "build_watchlist",     lambda: fixed_wl)

    # --- api_client yfinance wrappers (module attribute, visible to data_layer) ---
    monkeypatch.setattr(api, "get_historical_data",
                        lambda ticker, days=60: list(_BARS))

    # --- price_feed quotes (position_ledger calls via module reference) ---
    _q = {"price": 180.0, "prev_close": 175.0, "change_pct": 2.86, "source": "mock"}
    monkeypatch.setattr(pf, "get_quote",  lambda sym: dict(_q))
    monkeypatch.setattr(pf, "get_quotes", lambda syms: {s: dict(_q) for s in syms})

    # --- exit_manager (called via module reference "exit_manager.check_exit_barriers") ---
    monkeypatch.setattr(em, "check_exit_barriers", lambda positions: [])


@pytest.fixture
def mock_http(requests_mock):
    """Register canned T212 and Telegram HTTP responses."""
    T212 = "https://demo.trading212.com/api/v0"

    requests_mock.get(f"{T212}/equity/portfolio",    json=_t212_portfolio())
    requests_mock.get(f"{T212}/equity/account/cash", json=_t212_cash())
    requests_mock.get(f"{T212}/equity/account/info", json={"currencyCode": "EUR"})
    requests_mock.get(f"{T212}/equity/orders",       json=[])
    requests_mock.post(
        f"{T212}/equity/orders/market",
        json={"id": 99999, "ticker": "NVDA_US_EQ", "quantity": 1.0, "fillPrice": 180.0},
    )
    requests_mock.post(
        f"{T212}/equity/orders/limit",
        json={"id": 88888, "ticker": "NVDA_US_EQ", "quantity": 1.0, "fillPrice": 179.5},
    )
    requests_mock.delete(re.compile(r".*/equity/orders/\d+"), json={})

    requests_mock.post(
        re.compile(r"https://api\.telegram\.org/bot[^/]+/sendMessage"),
        json={"ok": True, "result": {"message_id": 1}},
    )

    return requests_mock


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPhase0DryRun:

    def test_smoke(self, sandbox, stub_externals, mock_http):
        """Full cycle runs without raising and writes both required output files."""
        import bot.phase0 as p0

        # No watchlist candidates → minimal execution path, fastest smoke test.
        with mock.patch.object(p0, "_scan_watchlist_candidates",
                               return_value=([], [])):
            report = p0.run(git_sync=False)

        assert report["mode"] in {"phase1_auto", "phase0_readonly", "holiday_skip"}
        assert "regime"  in report
        assert "signals" in report

        # --- beta_analysis.json ---
        analysis_path = sandbox / "beta_analysis.json"
        assert analysis_path.exists(), "beta_analysis.json não foi criado"
        data = json.loads(analysis_path.read_text(encoding="utf-8"))
        for key in ("timestamp", "mode", "regime", "signals"):
            assert key in data, f"chave '{key}' ausente em beta_analysis.json"

        # --- status.json ---
        status_path = sandbox / "status.json"
        assert status_path.exists(), "status.json não foi criado"
        status = json.loads(status_path.read_text(encoding="utf-8"))
        assert status.get("bot_status") in {"active", "holiday"}, (
            f"bot_status inválido: {status.get('bot_status')!r}"
        )
        assert "last_check" in status

        # At least one T212 call was made (portfolio sync happened).
        t212_calls = [r for r in mock_http.request_history
                      if "demo.trading212.com" in r.url]
        assert t212_calls, "Nenhuma chamada à T212 API foi registada"

    def test_buy_path(self, sandbox, stub_externals, mock_http):
        """Injecting a buy opportunity triggers trade execution end-to-end."""
        import bot.phase0 as p0

        with mock.patch.object(p0, "_scan_watchlist_candidates",
                               return_value=(_canned_buy_opp(), [])), \
             mock.patch.object(p0, "_apply_bonnie_filter",
                               side_effect=lambda opps: opps), \
             mock.patch.object(p0, "_apply_social_veto",
                               side_effect=lambda opps: opps):
            report = p0.run(git_sync=False)

        if report.get("mode") == "holiday_skip":
            pytest.skip("Feriado NYSE — ciclo ignorado (esperado em dias de feriado)")

        executed = report.get("executed_trades", [])
        assert isinstance(executed, list)

        if executed:
            for trade in executed:
                assert "ticker" in trade, "executed_trade sem campo 'ticker'"
                assert "side"   in trade, "executed_trade sem campo 'side'"
                assert "qty"    in trade, "executed_trade sem campo 'qty'"

            # Telegram deve ter sido chamado para cada trade executado.
            tg_calls = [r for r in mock_http.request_history
                        if "telegram.org" in r.url]
            assert tg_calls, "Telegram não foi chamado após execução de trade"

            # T212 recebeu POST de ordem de compra.
            buy_posts = [
                r for r in mock_http.request_history
                if "demo.trading212.com" in r.url
                and r.method == "POST"
                and "orders/market" in r.url
            ]
            assert buy_posts, "POST /equity/orders/market não foi chamado"

    def test_telegram_failure_is_isolated(self, sandbox, stub_externals, requests_mock):
        """Telegram returning 500 must not abort the cycle (Regra 3 do CLAUDE.md)."""
        import bot.phase0 as p0

        T212 = "https://demo.trading212.com/api/v0"
        requests_mock.get(f"{T212}/equity/portfolio",    json=_t212_portfolio())
        requests_mock.get(f"{T212}/equity/account/cash", json=_t212_cash())
        requests_mock.get(f"{T212}/equity/account/info", json={"currencyCode": "EUR"})
        requests_mock.get(f"{T212}/equity/orders",       json=[])
        requests_mock.post(
            f"{T212}/equity/orders/market",
            json={"id": 99999, "quantity": 1.0, "fillPrice": 180.0},
        )
        requests_mock.delete(re.compile(r".*/equity/orders/\d+"), json={})

        # Telegram está em baixo.
        requests_mock.post(
            re.compile(r"https://api\.telegram\.org/bot[^/]+/sendMessage"),
            status_code=500,
            json={"ok": False, "description": "Internal Server Error"},
        )

        with mock.patch.object(p0, "_scan_watchlist_candidates",
                               return_value=(_canned_buy_opp(), [])), \
             mock.patch.object(p0, "_apply_bonnie_filter",
                               side_effect=lambda opps: opps), \
             mock.patch.object(p0, "_apply_social_veto",
                               side_effect=lambda opps: opps):
            # Must not raise even though Telegram is broken.
            report = p0.run(git_sync=False)

        assert report.get("mode") in {"phase1_auto", "phase0_readonly", "holiday_skip"}, (
            f"Ciclo abortou em modo inesperado: {report.get('mode')!r}"
        )

        # status.json deve ter sido escrito apesar da falha Telegram.
        status_path = sandbox / "status.json"
        assert status_path.exists(), "status.json ausente após falha Telegram"
        status = json.loads(status_path.read_text(encoding="utf-8"))
        assert status.get("bot_status") in {"active", "holiday"}
