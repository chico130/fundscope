---
id: moc-bonnie
title: "MOC — Bonnie (Filtro de Risco por Trade)"
type: moc
domain: bonnie
regime: n/a
tags: [moc, bonnie, risco, filtro, veto, aprendizagem]
links_obrigatorios:
  parent_moc: "[[MOC_FundScope]]"
  vizinhos: "[[MOC_Clyde]] [[MOC_CRO]] [[MOC_Infraestrutura]]"
status: stable
ultima_revisao: 2026-05-19
---

# MOC — [[MOC_Bonnie|Bonnie]] (Filtro de Risco por Trade)

> [[MOC_Bonnie|Bonnie]] é o guarda-chuva de risco individual: aprova ou veta cada trade proposto pelo [[MOC_Clyde|Clyde]] antes da execução.

Hub: [[MOC_FundScope]] → este MOC → módulos de filtro e aprendizagem.

---

## Módulos Principais

| Ficheiro | Responsabilidade |
|---|---|
| [[bonnie.py]] | Filtro de risco por trade individual (aprovação/bloqueio) |
| [[learner.py]] | Análise de trades fechados, deteção de padrões de erro |
| [[evaluate_bonnie.py]] | Avaliação de performance da [[MOC_Bonnie|Bonnie]] |
| [[model_trainer.py]] | Treino de modelos ML para predição de risco |
| [[position_ledger.py]] | Registo de posições e histórico de P&L |

---

## Regras de Veto ([[MOC_Bonnie|Bonnie]] Filter)

```
VETO se qualquer condição:
  1. Posição já existe no ticker (sem duplicação)
  2. Exposição de setor > 40% do portfolio
  3. Posição individual > 20% do portfolio
  4. Perda diária acumulada > 3% (max_daily_loss_pct)
  5. Nº de trades no dia > 10 (max_trades)
  6. Ticker tem earnings em < 2 dias (earnings_risk)
  7. SL implícito > 5% do preço de entrada
```

---

## Limites de Risco ([[MOC_Infraestrutura|config_risco.json]])

| Parâmetro | Valor |
|---|---|
| [[MOC_Bonnie|max_position_pct]] | 20% |
| max_sector_pct | 40% |
| [[MOC_Bonnie|max_daily_loss_pct]] | 3% |
| max_trades_per_day | 10 |
| stop_loss_pct | 5% |
| take_profit_pct | 10% |
| no_trade_before_earnings | 2 dias |

---

## Ciclo de Aprendizagem (Smart Money Gate)

```
[[execution.py]] → trade executado
      ↓
[[position_ledger.py]] → registo P&L
      ↓
[[learner.py]] → análise de padrões de erro (Calmar, Profit Factor)
      ↓
[[model_trainer.py]] → actualiza parâmetros Bonnie
      ↓
[[bonnie.py]] → thresholds actualizados
```

---

## Logs e Observabilidade

- `logs/bonnie_log.json` — snapshot de estado: fase, config_risco, estatísticas, vetos, win_rate, alertas
- `data/beta/beta_trades.json` — diário de trades com contexto técnico completo
- `data/beta/positions_ledger.json` — estado de posições open/closed

---

## Fronteira com o [[MOC_CRO|CRO]]

[[MOC_Bonnie|Bonnie]] filtra risco **por trade individual**. O [[MOC_CRO]] filtra risco **sistémico** (equity curve, [[MOC_CRO|regime macro]], [[MOC_CRO|kill-switch]]). São complementares, não redundantes.

---

## Ligações Cruzadas

- [[MOC_Clyde]] — recebe sinais de [[MOC_Clyde|Clyde]] para aprovação/veto
- [[MOC_CRO]] — [[MOC_CRO|CRO]] lê [[MOC_Bonnie|bonnie_log.json]]; [[MOC_Bonnie|Bonnie]] respeita [[MOC_CRO|kill-switch]] do [[MOC_CRO|CRO]]
- [[MOC_Infraestrutura]] — [[config|config.py]] e [[data_layer|data_layer.py]] alimentam os thresholds
- [[FASE-1]] — roadmap: learner emergency breaker, [[atom-calmar|Calmar ratio]]
- [[vault/specs/FUNDSCOPE_CLAUDE_CODE_SPEC]] — especificação dos parâmetros [[MOC_Bonnie|Bonnie]]
