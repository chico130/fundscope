---
id: backlog
title: "Backlog — Próximas Melhorias"
type: spec
domain: bonnie
regime: n/a
tags: [backlog, planeamento, bonnie-v5, draft]
links_obrigatorios:
  parent_moc: "[[MOC_FundScope]]"
  vizinhos: "[[run-007]] [[MOC_Bonnie]]"
status: draft
ultima_revisao: 2026-05-24
---

# Backlog — Próximas Melhorias

> **Navegação:** [↑ Índice](../000-INDEX.md) · ver também [run-007](run-007.md)
**Data de desbloqueio: 24 Jun 2026**
Não implementar antes de 30 dias de produção real.

## P1 — Redesenhar Target do Modelo (Opus)
Mudar target binário success/failure para excess
return contínuo: y = R_asset - R_SPY por trade.
Implica mudar GradientBoostingClassifier → Regressor.
Ver prompt completo em: docs/prompts/P1_excess_return.md

## P2 — 10 Novas Features (Opus)
Features calculáveis via OHLCV + macro gratuito,
com evidência académica, sem lookahead bias.
Ver prompt completo em: docs/prompts/P2_features.md

## P4 — Implementar Excess Return Target (Sonnet)
Implementação do P1 com threshold adaptativo
por regime (bull/bear/sideways).
Ver prompt completo em: docs/prompts/P4_regressor.md

## Bonnie v5
LABEL_HORIZON_DAYS: 20 → 57 dias
Label balance esperado: 15.8% → ~30-40%
F1 real deve melhorar significativamente.
Comando: python scripts/retrain_bonnie.py \
  --since 2017-01-01 --until 2026-05-01 \
  --model-version v5 --tp-mult 4.25 --sl-mult 1.75 \
  --label-horizon 57

---
*Sistema actual: v3 + Bonnie v4-clean*
*OOS +62.2% vs SPY +45.2% · Sharpe 2.09*
