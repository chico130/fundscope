"""
report.py — Escreve os artefactos de saída do sweep de calibração.

Artefactos:
  data/calibration/sweep_results.csv  — grelha completa
  data/calibration/REPORT.md          — sumário legível (markdown)
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from bot.config import BASE_DIR

_OUT_DIR         = BASE_DIR / "data" / "calibration"
_CSV_PATH        = _OUT_DIR / "sweep_results.csv"
_REPORT_PATH     = _OUT_DIR / "REPORT.md"
_OOS_REPORT_PATH = _OUT_DIR / "OOS_REPORT.md"
_OOS_CSV_PATH    = _OUT_DIR / "oos_report.csv"

_TOP_N       = 15
_PF_ROBUST   = 1.5     # limiar "edge robusto" (atom-profit-factor)


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def write_report(
    sweep:       pd.DataFrame,
    universe_n:  int,
    start:       str,
    end:         str,
    horizons:    list[int],
) -> None:
    """Grava CSV + REPORT.md com o resultado do sweep."""
    _OUT_DIR.mkdir(parents=True, exist_ok=True)

    sweep.to_csv(_CSV_PATH, index=False)
    print(f"[report] CSV gravado → {_CSV_PATH}")

    md = _build_markdown(sweep, universe_n, start, end, horizons)
    _REPORT_PATH.write_text(md, encoding="utf-8")
    print(f"[report] REPORT.md gravado → {_REPORT_PATH}")


# ---------------------------------------------------------------------------
# Construção do markdown
# ---------------------------------------------------------------------------

def _build_markdown(
    sweep:      pd.DataFrame,
    universe_n: int,
    start:      str,
    end:        str,
    horizons:   list[int],
) -> str:

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    total_combos  = len(sweep)
    valid  = sweep[~sweep["low_sample"].fillna(True)]
    prod   = sweep[sweep["is_production"].fillna(False)]

    lines: list[str] = []

    # Cabeçalho
    lines += [
        "# FundScope — Relatório de Calibração de Parâmetros",
        "",
        f"**Gerado em:** {generated}",
        f"**Universo:** S&P 500 ({universe_n} tickers, composição atual)",
        f"**Janela:** {start} → {end}",
        f"**Horizontes avaliados:** {horizons} dias de trading",
        f"**Combinações totais:** {total_combos} ({len(valid)} com amostra suficiente)",
        "",
        "> ⚠️ **Viés de sobrevivência:** este relatório usa a composição *atual* do S&P 500.",
        "> Empresas removidas do índice (falências, quedas) não estão incluídas —",
        "> os resultados estão enviesados para cima. Fase 2 corrigirá com membership histórico.",
        "",
    ]

    # Parâmetros de produção atuais
    lines += [
        "---",
        "",
        "## 1. Parâmetros de Produção (estado atual)",
        "",
        "| Parâmetro | Valor atual |",
        "|---|---|",
        "| RSI_BUY_MAX | 34.0 |",
        "| VOL_RATIO_MIN | 1.2 |",
        "| EMA50 > EMA200 (gate) | True |",
        "| Veto de regime bear | True |",
        "| Distância EMA50 mín. (nova feature) | — |",
        "",
    ]

    if prod.empty:
        lines += ["*Parâmetros de produção não encontrados no sweep.*", ""]
    else:
        for H in horizons:
            rows_h = prod[prod["horizon"] == H]
            if rows_h.empty:
                continue
            r = rows_h.iloc[0]
            lines += [
                f"### Produção · Horizonte {H}d",
                "",
                f"| Métrica | Valor |",
                f"|---|---|",
                f"| Trades gerados | {int(r.get('n_trades', 0)):,} |",
                f"| Win Rate | {r.get('win_rate', float('nan')):.1%} |",
                f"| Profit Factor | {r.get('profit_factor', float('nan')):.3f} |",
                f"| Expectancy | {r.get('expectancy_pct', float('nan')):+.2f}% |",
                f"| Total Return | {r.get('total_return_pct', float('nan')):+.1f}% |",
                f"| Low sample? | {'⚠ SIM' if r.get('low_sample') else 'não'} |",
                "",
            ]
            if int(r.get("n_trades", 0)) == 0:
                lines += [
                    "**🚨 Diagnóstico confirmado: os filtros atuais não geram trades nesta janela.**",
                    "A combinação RSI≤35 + EMA50>EMA200 é estruturalmente rara — sobrevenda",
                    "e tendência de alta puxam em direções opostas.",
                    "",
                ]

    # Top-15 por Profit Factor
    lines += _top_table(valid, "profit_factor", "2. Top 15 — Profit Factor", horizons)

    # Top-15 por Win Rate
    lines += _top_table(valid, "win_rate", "3. Top 15 — Win Rate", horizons)

    # Top-15 por Expectancy
    lines += _top_table(valid, "expectancy_pct", "4. Top 15 — Expectancy (retorno médio/trade)", horizons)

    # Análise: booleano vs distância EMA50
    lines += _ema50_comparison(valid, horizons)

    # Rodapé
    lines += [
        "---",
        "",
        "## 6. Notas e Próximos Passos",
        "",
        "- Este relatório é **in-sample**: os parâmetros foram encontrados nos mesmos dados que avaliam.",
        "  Validar em período holdout ou walk-forward antes de aplicar em produção.",
        "- **Sem custos de transação:** PF e WR são pré-custo.",
        "  Spread/slippage vão degradar os números — estimar na fase 2.",
        "- **Recomendação de ajuste:** escolher a combinação com melhor equilíbrio PF/WR/n_trades",
        "  e atualizar `RSI_BUY_MAX` e `VOL_RATIO_MIN` em `bot/backtest.py`.",
        "  Nunca alterar os ficheiros de produção (`price_feed.py`, `phase0.py`).",
        "",
        f"*FundScope Calibration Engine — {generated}*",
    ]

    return "\n".join(lines)


def _top_table(
    df:      pd.DataFrame,
    sort_by: str,
    title:   str,
    horizons: list[int],
) -> list[str]:
    """Gera secção markdown com top-N combinações ordenadas por `sort_by`."""
    lines = [
        "---",
        "",
        f"## {title}",
        "",
    ]

    for H in horizons:
        sub = df[df["horizon"] == H].copy()
        if sub.empty:
            continue
        sub = sub.sort_values(sort_by, ascending=False).head(_TOP_N)

        lines += [
            f"### Horizonte {H} dias",
            "",
            "| # | RSI≤ | Vol≥ | EMA50>200 | EMA50dist≥ | Regime veto | Trades | WR | PF | Expect% |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]

        for rank, (_, r) in enumerate(sub.iterrows(), 1):
            ema_dist = f"{r['ema50_dist_min_pct']:+.0f}%" if r.get("ema50_dist_min_pct") is not None and str(r.get("ema50_dist_min_pct")) not in ("nan", "None") else "—"
            pf_val   = r.get("profit_factor", float("nan"))
            pf_str   = f"**{pf_val:.3f}**" if pf_val >= _PF_ROBUST else f"{pf_val:.3f}"
            lines.append(
                f"| {rank} "
                f"| {r['rsi_buy_max']:.0f} "
                f"| {r['vol_ratio_min']:.1f} "
                f"| {'✓' if r['require_ema50_above_200'] else '✗'} "
                f"| {ema_dist} "
                f"| {'✓' if r['apply_regime_veto'] else '✗'} "
                f"| {int(r.get('n_trades', 0)):,} "
                f"| {r.get('win_rate', float('nan')):.1%} "
                f"| {pf_str} "
                f"| {r.get('expectancy_pct', float('nan')):+.2f}% |"
            )

        lines.append("")

    return lines


def write_oos_report(
    oos_report: pd.DataFrame,
    horizons: list[int],
    train_end: str,
    val_start: str,
    n_folds: int = 1,
    n_min: int = 30,
    pf_drop_threshold: float = 0.40,
) -> None:
    """Grava data/calibration/OOS_REPORT.md e data/calibration/oos_report.csv."""
    _OUT_DIR.mkdir(parents=True, exist_ok=True)

    oos_report.to_csv(_OOS_CSV_PATH, index=False)
    print(f"[report] CSV OOS gravado → {_OOS_CSV_PATH}")

    md = _build_oos_markdown(oos_report, horizons, train_end, val_start, n_folds, n_min, pf_drop_threshold)
    _OOS_REPORT_PATH.write_text(md, encoding="utf-8")
    print(f"[report] OOS_REPORT.md gravado → {_OOS_REPORT_PATH}")


def _build_oos_markdown(
    oos: pd.DataFrame,
    horizons: list[int],
    train_end: str,
    val_start: str,
    n_folds: int,
    n_min: int,
    pf_drop_threshold: float,
) -> str:
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    fold_desc = (
        f"Walk-forward {n_folds}-fold"
        if n_folds > 1
        else f"Divisão simples (treino até {train_end} / validação a partir de {val_start})"
    )

    lines: list[str] = [
        "# FundScope — Relatório de Validação Out-of-Sample (OOS)",
        "",
        f"**Gerado em:** {generated}",
        f"**Período de treino:** até {train_end}",
        f"**Período de validação:** a partir de {val_start}",
        f"**Método:** {fold_desc}",
        f"**Limiar de queda PF aceitável:** {pf_drop_threshold:.0%}",
        f"**N mínimo de trades:** {n_min}",
        "",
        "> Os parâmetros foram encontrados **exclusivamente** no período de treino.",
        "> O período de validação nunca influenciou a selecção de parâmetros.",
        "",
    ]

    if oos.empty:
        lines += ["*Sem dados OOS para apresentar.*", ""]
        return "\n".join(lines)

    # Tabela principal
    lines += [
        "---",
        "",
        "## Resultados por Horizonte",
        "",
        "| Horizonte | RSI≤ | Vol≥ "
        "| PF Treino | Expect% Treino | MaxDD% Treino "
        "| PF Validação | Expect% Validação | MaxDD% Validação "
        "| Queda PF% | Pontuação Robustez | Estado |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]

    for _, r in oos.iterrows():
        def _pf(v: object) -> str:
            return f"{float(v):.3f}" if _is_num(v) else "—"

        def _exp(v: object) -> str:
            return f"{float(v):+.2f}%" if _is_num(v) else "—"

        def _dd(v: object) -> str:
            return f"{float(v):+.1f}%" if _is_num(v) else "—"

        def _drop(v: object) -> str:
            return f"{float(v):.1%}" if _is_num(v) else "—"

        def _rob(v: object) -> str:
            return f"{float(v):.1f}" if _is_num(v) else "—"

        lines.append(
            f"| {int(r['horizon'])}d "
            f"| {r['rsi_buy_max']:.0f} "
            f"| {r['vol_ratio_min']:.1f} "
            f"| {_pf(r.get('train_pf'))} "
            f"| {_exp(r.get('train_expectancy_pct'))} "
            f"| {_dd(r.get('train_maxdd_pct'))} "
            f"| {_pf(r.get('val_pf'))} "
            f"| {_exp(r.get('val_expectancy_pct'))} "
            f"| {_dd(r.get('val_maxdd_pct'))} "
            f"| {_drop(r.get('pf_drop_pct'))} "
            f"| {_rob(r.get('robustness_score'))} "
            f"| {r.get('status', '—')} |"
        )

    lines += ["", "---", "", "## Resumo"]
    lines += _oos_summary(oos, horizons)

    lines += [
        "",
        "---",
        "",
        "## Notas",
        "",
        "- **Pontuação Robustez** = 100 × (1 − queda_PF) × min(1, n_trades_val / n_min).",
        "- Estratégias com **⚠️ DADOS INSUFICIENTES** têm trades de validação abaixo do limiar mínimo.",
        "- Uma queda de PF elevada pode indicar overfitting aos dados de treino.",
        "",
        f"*FundScope Calibration Engine — {generated}*",
    ]

    return "\n".join(lines)


def _oos_summary(oos: pd.DataFrame, horizons: list[int]) -> list[str]:
    """Secção de resumo: contagens por status e avisos por horizonte."""
    import math

    n_valid       = (oos["status"] == "✅ VÁLIDO").sum()
    n_overfitted  = (oos["status"] == "🚨 OVERFITTED").sum()
    n_insuff      = (oos["status"] == "⚠️ DADOS INSUFICIENTES").sum()

    lines: list[str] = [
        "",
        f"- ✅ **Estratégias válidas:** {n_valid}",
        f"- 🚨 **Estratégias overfitted:** {n_overfitted}",
        f"- ⚠️ **Dados insuficientes:** {n_insuff}",
        "",
    ]

    for H in horizons:
        sub = oos[oos["horizon"] == H]
        if sub.empty:
            continue
        if (sub["status"] == "🚨 OVERFITTED").all():
            lines.append(
                f"> 🔴 **AVISO:** Todas as estratégias do horizonte **{H}d** estão overfitted. "
                "Não aplicar estes parâmetros em produção."
            )

    return lines


def _is_num(v: object) -> bool:
    """Verdadeiro se v é um número finito (não NaN, não None)."""
    import math
    try:
        return v is not None and not math.isnan(float(v))
    except (TypeError, ValueError):
        return False


def _ema50_comparison(df: pd.DataFrame, horizons: list[int]) -> list[str]:
    """Secção: gate booleano ema50>200 vs distância contínua."""
    lines = [
        "---",
        "",
        "## 5. EMA50: Booleano vs Distância Contínua",
        "",
        "Compara as melhores combinações que usam o gate booleano (produção atual)",
        "com as que usam a distância percentual contínua ao EMA50 (nova feature).",
        "",
    ]

    for H in horizons:
        sub = df[df["horizon"] == H].copy()
        if sub.empty:
            continue

        bool_best = (
            sub[sub["require_ema50_above_200"] & sub["ema50_dist_min_pct"].isna()]
            .sort_values("profit_factor", ascending=False)
            .head(3)
        )
        dist_best = (
            sub[sub["ema50_dist_min_pct"].notna()]
            .sort_values("profit_factor", ascending=False)
            .head(3)
        )

        lines += [
            f"### Horizonte {H}d",
            "",
            "**Gate booleano (ema50 > ema200):**",
            "",
            "| RSI≤ | Vol≥ | Trades | WR | PF |",
            "|---|---|---|---|---|",
        ]
        for _, r in bool_best.iterrows():
            lines.append(
                f"| {r['rsi_buy_max']:.0f} | {r['vol_ratio_min']:.1f}"
                f"| {int(r.get('n_trades', 0)):,} "
                f"| {r.get('win_rate', float('nan')):.1%} "
                f"| {r.get('profit_factor', float('nan')):.3f} |"
            )

        lines += [
            "",
            "**Distância EMA50 contínua:**",
            "",
            "| RSI≤ | Vol≥ | Dist≥ | Trades | WR | PF |",
            "|---|---|---|---|---|---|",
        ]
        for _, r in dist_best.iterrows():
            ema_dist = f"{r['ema50_dist_min_pct']:+.0f}%" if r.get("ema50_dist_min_pct") is not None else "—"
            lines.append(
                f"| {r['rsi_buy_max']:.0f} | {r['vol_ratio_min']:.1f} | {ema_dist}"
                f"| {int(r.get('n_trades', 0)):,} "
                f"| {r.get('win_rate', float('nan')):.1%} "
                f"| {r.get('profit_factor', float('nan')):.3f} |"
            )

        lines.append("")

    return lines
