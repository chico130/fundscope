"""
adaptive.py — Motor de recalibração adaptativa do Clyde (offline).

Detecta automaticamente quando a performance dos parâmetros activos degrada ou
quando o regime de mercado muda, e recalibra contra o histórico recente usando
o protocolo Out-of-Sample já existente (sweep.run_sweep_oos).

Parede de fogo
--------------
Este módulo NÃO importa nada do bot de produção (price_feed, phase0, api_client,
strategy, learner). Importa exclusivamente do próprio módulo bot.calibration.
Os caminhos de ficheiro são derivados de __file__ para evitar até a dependência
de bot.config. Nenhum ficheiro de produção é lido ou alterado.

O parâmetro ema50_dist_min_pct — identificado na Fase 1 como o mais importante
(PF=2.16) — já está integrado na grelha de sweep (sweep.DEFAULT_GRID) e no
baseline (strategy_baseline.json), pelo que a recalibração optimiza-o nativamente
sem qualquer alteração ao learner online de produção.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from bot.calibration.sweep import (
    DEFAULT_GRID,
    ParamSet,
    _build_mask,
    run_sweep_oos,
)
from bot.calibration.metrics import compute_metrics_full
from bot.calibration.regime import BEAR_REGIMES

# BASE_DIR derivado do próprio ficheiro (bot/calibration/adaptive.py → fundscope/)
_BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_BASELINE_PATH = Path(__file__).with_name("strategy_baseline.json")
DEFAULT_LOG_PATH = _BASE_DIR / "data" / "calibration" / "adaptive_log.jsonl"

# Os 5 parâmetros que definem um ParamSet (sem o horizonte).
_PARAM_KEYS = (
    "rsi_buy_max",
    "vol_ratio_min",
    "require_ema50_above_200",
    "ema50_dist_min_pct",
    "apply_regime_veto",
)

# Status devolvido por sweep._compute_status para uma validação OOS aprovada.
_OOS_VALID_PREFIX = "✅"

# Janela de treino para recalibração: últimos 2 anos de trading.
_TRAIN_LOOKBACK_DAYS = 365 * 2


class AdaptiveCalibrator:
    """
    Avalia a performance recente dos parâmetros activos e recalibra-os quando
    o mercado muda de regime ou a performance degrada.

    Toda a optimização passa pelo protocolo OOS (sweep.run_sweep_oos): um novo
    conjunto de parâmetros só substitui o baseline se for considerado VÁLIDO
    (queda de Profit Factor entre treino e validação dentro do limiar) E melhorar
    o Profit Factor face ao baseline actual.
    """

    def __init__(
        self,
        baseline_path: Path = DEFAULT_BASELINE_PATH,
        recal_trigger_pf: float = 0.9,
        lookback_days: int = 90,
        min_trades_to_evaluate: int = 20,
    ) -> None:
        """
        Parâmetros
        ----------
        baseline_path : Path
            Caminho para strategy_baseline.json (escrito pela Fase 1).
        recal_trigger_pf : float
            Recalibrar se o Profit Factor recente cair abaixo deste valor.
        lookback_days : int
            Janela deslizante (dias de calendário) de avaliação da performance
            actual. Também serve de intervalo de manutenção periódica.
        min_trades_to_evaluate : int
            Mínimo de trades na janela para a avaliação ser considerada fiável.
            Nunca é reduzido — apenas reportado quando não é atingido (alinhado
            com os mínimos do learner: _MIN_TRADES_WEEKLY=20).
        """
        self.baseline_path = Path(baseline_path)
        self.recal_trigger_pf = recal_trigger_pf
        self.lookback_days = lookback_days
        self.min_trades_to_evaluate = min_trades_to_evaluate
        self.baseline = self._load_baseline()

    # ------------------------------------------------------------------ #
    # Baseline I/O
    # ------------------------------------------------------------------ #

    def _load_baseline(self) -> dict:
        """Lê o baseline JSON; erro explícito se não existir."""
        if not self.baseline_path.exists():
            raise FileNotFoundError(
                f"Baseline não encontrado em {self.baseline_path}. "
                "Corre a calibração da Fase 1 primeiro."
            )
        return json.loads(self.baseline_path.read_text(encoding="utf-8"))

    @property
    def active_params(self) -> dict:
        """Os 5 parâmetros actualmente activos (best_params do baseline)."""
        bp = self.baseline.get("best_params", {})
        return {k: bp.get(k) for k in _PARAM_KEYS}

    @property
    def horizon(self) -> int:
        """Horizonte de avaliação em dias (do baseline; default 10)."""
        return int(self.baseline.get("horizon", 10))

    @property
    def baseline_pf(self) -> float:
        """Profit Factor registado no baseline."""
        return float(self.baseline.get("metrics", {}).get("profit_factor", float("nan")))

    # ------------------------------------------------------------------ #
    # Avaliação da performance actual
    # ------------------------------------------------------------------ #

    def evaluate_current_performance(self, cand: pd.DataFrame) -> dict:
        """
        Avalia os últimos `lookback_days` com os parâmetros activos do baseline.

        Âncora temporal: a data mais recente presente em `cand` (robusto a dados
        em atraso). A janela é [âncora − lookback_days, âncora].

        Devolve
        -------
        dict com:
            pf_current, expectancy_current, win_rate, n_trades, regime,
            low_sample (bool), lookback_start, lookback_end, needs_recal (bool)
        """
        anchor = pd.to_datetime(cand["date"]).max()
        win_start = anchor - pd.Timedelta(days=self.lookback_days)
        subset = cand[pd.to_datetime(cand["date"]) >= win_start]

        metrics = self._eval_params(subset, self.active_params)
        regime = self._dominant_regime(subset)

        perf = {
            "pf_current": metrics.get("profit_factor", float("nan")),
            "expectancy_current": metrics.get("expectancy_pct", float("nan")),
            "win_rate": metrics.get("win_rate", float("nan")),
            "n_trades": int(metrics.get("n_trades", 0)),
            "regime": regime,
            "low_sample": bool(metrics.get("low_sample", True)),
            "lookback_start": win_start.strftime("%Y-%m-%d"),
            "lookback_end": anchor.strftime("%Y-%m-%d"),
        }
        perf["needs_recal"] = self.should_recalibrate(perf, cand)
        return perf

    def should_recalibrate(self, perf: dict, cand: pd.DataFrame | None = None) -> bool:
        """
        Regras de trigger (OR):
          1. Degradação: pf_current < recal_trigger_pf (só se amostra suficiente).
          2. Mudança de regime: família de regime actual ≠ família do baseline.
          3. Manutenção periódica: última recalibração há > lookback_days dias.

        `cand` é opcional e só é usado para derivar o regime do baseline quando
        este não está gravado no ficheiro.
        """
        # 1. Degradação de performance (exige amostra fiável)
        pf = perf.get("pf_current", float("nan"))
        if not perf.get("low_sample", True) and not pd.isna(pf):
            if pf < self.recal_trigger_pf:
                return True

        # 2. Mudança de regime de mercado
        base_regime = self._baseline_regime(cand)
        if base_regime is not None and perf.get("regime") is not None:
            if _regime_family(perf["regime"]) != _regime_family(base_regime):
                return True

        # 3. Manutenção periódica
        if self._days_since_last_recal() > self.lookback_days:
            return True

        return False

    # ------------------------------------------------------------------ #
    # Recalibração
    # ------------------------------------------------------------------ #

    def recalibrate(self, cand: pd.DataFrame) -> dict:
        """
        Recalibra os parâmetros via protocolo OOS:
          1. run_sweep_oos() — treino nos últimos 2 anos, validação nos últimos
             `lookback_days` dias.
          2. Escolhe o melhor ParamSet do horizonte do baseline.
          3. Valida OOS — só actualiza o baseline se VÁLIDO **e** melhor PF.

        Devolve
        -------
        dict com: old_params, new_params, improvement_pct, action
          action ∈ {"UPDATED", "NO_CHANGE", "INSUFFICIENT_DATA"}
        """
        old_params = self.active_params
        H = self.horizon

        anchor = pd.to_datetime(cand["date"]).max()
        val_start = anchor - pd.Timedelta(days=self.lookback_days)
        train_end = val_start - pd.Timedelta(days=1)
        train_start = anchor - pd.Timedelta(days=_TRAIN_LOOKBACK_DAYS)

        cand_window = cand[pd.to_datetime(cand["date"]) >= train_start].copy()

        _, sweep_val, oos_report = run_sweep_oos(
            cand_window,
            horizons=[H],
            train_end=train_end.strftime("%Y-%m-%d"),
            val_start=val_start.strftime("%Y-%m-%d"),
            grid=DEFAULT_GRID,
            n_min=self.min_trades_to_evaluate,
            n_folds=1,
        )

        oos_row = self._row_for_horizon(oos_report, H)
        val_row = self._row_for_horizon(sweep_val, H)

        if oos_row is None or val_row is None:
            return self._result(old_params, None, 0.0, "INSUFFICIENT_DATA")

        status = str(oos_row.get("status", ""))
        if not status.startswith(_OOS_VALID_PREFIX):
            # DADOS INSUFICIENTES ou OVERFITTED → não mexer no baseline.
            action = (
                "INSUFFICIENT_DATA"
                if "INSUFICIENTES" in status or "INSUFICIENTE" in status
                else "NO_CHANGE"
            )
            return self._result(old_params, None, 0.0, action)

        new_params = {k: _native(val_row.get(k)) for k in _PARAM_KEYS}
        new_pf = float(val_row.get("profit_factor", float("nan")))
        old_pf = self.baseline_pf
        improvement_pct = self._improvement_pct(old_pf, new_pf)

        if pd.isna(new_pf) or improvement_pct <= 0.0:
            # Validou mas não melhora o baseline — manter parâmetros actuais.
            return self._result(old_params, new_params, improvement_pct, "NO_CHANGE")

        self._write_baseline(
            new_params=new_params,
            val_row=val_row,
            train_start=train_start,
            anchor=anchor,
            regime=self._dominant_regime(
                cand[pd.to_datetime(cand["date"]) >= val_start]
            ),
            old_params=old_params,
        )
        return self._result(old_params, new_params, improvement_pct, "UPDATED")

    # ------------------------------------------------------------------ #
    # Logging
    # ------------------------------------------------------------------ #

    def log_run(self, perf: dict, action: str, log_path: Path = DEFAULT_LOG_PATH) -> None:
        """Acrescenta uma linha JSONL ao log adaptativo (uma por execução)."""
        log_path = Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "pf_current": _round(perf.get("pf_current")),
            "needs_recal": bool(perf.get("needs_recal", False)),
            "action": action,
            "regime": perf.get("regime"),
            "params_active": self.active_params,
        }
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------------ #
    # Auxiliares internos
    # ------------------------------------------------------------------ #

    def _eval_params(self, cand: pd.DataFrame, params: dict) -> dict:
        """Avalia um conjunto fixo de parâmetros sobre `cand` (sem optimização)."""
        if cand.empty:
            return {"n_trades": 0, "profit_factor": float("nan"),
                    "expectancy_pct": float("nan"), "win_rate": float("nan"),
                    "low_sample": True}
        p = ParamSet(
            rsi_buy_max=params["rsi_buy_max"],
            vol_ratio_min=params["vol_ratio_min"],
            require_ema50_above_200=bool(params["require_ema50_above_200"]),
            ema50_dist_min_pct=params.get("ema50_dist_min_pct"),
            apply_regime_veto=bool(params["apply_regime_veto"]),
            horizon=self.horizon,
        )
        mask = _build_mask(cand, p, self.horizon)
        return compute_metrics_full(cand, mask, self.horizon, self.min_trades_to_evaluate)

    @staticmethod
    def _dominant_regime(cand: pd.DataFrame) -> str | None:
        """Regime predominante (moda) na janela; None se indisponível."""
        if cand.empty or "regime" not in cand.columns:
            return None
        modes = cand["regime"].mode()
        return str(modes.iloc[0]) if not modes.empty else None

    def _baseline_regime(self, cand: pd.DataFrame | None) -> str | None:
        """
        Regime de referência do baseline. Usa o campo gravado se existir;
        caso contrário, deriva o regime predominante na janela de dados do
        baseline a partir de `cand`. None se não for possível determinar.
        """
        stored = self.baseline.get("regime")
        if stored:
            return str(stored)
        if cand is None or "regime" not in cand.columns:
            return None
        window = self.baseline.get("data_window", {})
        start, end = window.get("start"), window.get("end")
        if not start or not end:
            return None
        dates = pd.to_datetime(cand["date"])
        sub = cand[(dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))]
        return self._dominant_regime(sub)

    def _days_since_last_recal(self) -> int:
        """Dias desde a data registada no baseline; +inf efectivo se ausente."""
        raw = self.baseline.get("date")
        if not raw:
            return 10**6
        try:
            last = datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            return 10**6
        return (date.today() - last).days

    @staticmethod
    def _row_for_horizon(df: pd.DataFrame, H: int) -> dict | None:
        """Devolve a linha (dict) do horizonte H, ou None se não existir."""
        if df is None or df.empty or "horizon" not in df.columns:
            return None
        sub = df[df["horizon"] == H]
        return None if sub.empty else sub.iloc[0].to_dict()

    @staticmethod
    def _improvement_pct(old_pf: float, new_pf: float) -> float:
        """Melhoria percentual de PF; 0.0 se o baseline não tiver PF válido."""
        if pd.isna(old_pf) or pd.isna(new_pf) or old_pf <= 0:
            return 0.0
        return round(100.0 * (new_pf - old_pf) / old_pf, 4)

    @staticmethod
    def _result(old: dict, new: dict | None, improvement: float, action: str) -> dict:
        return {
            "old_params": old,
            "new_params": new,
            "improvement_pct": improvement,
            "action": action,
        }

    def _write_baseline(
        self,
        new_params: dict,
        val_row: dict,
        train_start: pd.Timestamp,
        anchor: pd.Timestamp,
        regime: str | None,
        old_params: dict,
    ) -> None:
        """Reescreve strategy_baseline.json com os novos parâmetros validados."""
        updated = dict(self.baseline)  # preserva campos não tocados (ex.: notes)
        updated["best_params"] = new_params
        updated["metrics"] = {
            "profit_factor": _round(val_row.get("profit_factor")),
            "expectancy_pct": _round(val_row.get("expectancy_pct")),
            "win_rate": _round(val_row.get("win_rate")),
        }
        updated["data_window"] = {
            "start": train_start.strftime("%Y-%m-%d"),
            "end": anchor.strftime("%Y-%m-%d"),
            "note": "Janela de recalibração adaptativa (treino 2a + validação OOS).",
        }
        updated["horizon"] = self.horizon
        updated["n_trades"] = int(val_row.get("n_trades", 0))
        updated["date"] = date.today().strftime("%Y-%m-%d")
        updated["regime"] = regime
        updated["recalibrated_by"] = "adaptive_calibrator"
        updated["previous_params"] = old_params

        self.baseline_path.write_text(
            json.dumps(updated, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.baseline = updated  # active_params passa a reflectir os novos


# ---------------------------------------------------------------------------
# Funções livres
# ---------------------------------------------------------------------------

def _regime_family(regime: str | None) -> str:
    """Reduz o regime a 'bear'/'bull'/'unknown' para comparação robusta."""
    if regime is None:
        return "unknown"
    if regime in BEAR_REGIMES:
        return "bear"
    if regime.startswith("bull"):
        return "bull"
    return "unknown"


def _native(value: object) -> object:
    """Converte tipos numpy/pandas para tipos nativos serializáveis em JSON."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def _round(value: object, ndigits: int = 4) -> float | None:
    """Arredonda para JSON; None se NaN/inválido."""
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else round(f, ndigits)
