---
id: moc-clyde
title: "MOC — Clyde (Motor de Execução)"
type: moc
domain: clyde
regime: n/a
tags: [moc, clyde, execucao, sinais, trading]
links_obrigatorios:
  parent_moc: "[[MOC_FundScope]]"
  vizinhos: "[[MOC_Bonnie]] [[MOC_CRO]] [[MOC_Infraestrutura]]"
status: stable
ultima_revisao: 2026-05-19
---

# MOC — Clyde (Motor de Execução)

> Clyde é o executor do FundScope: gera sinais técnicos e submete ordens à Trading212.

Hub: [[MOC_FundScope]] → este MOC → módulos de sinal e execução.

---

## Módulos Principais

| Ficheiro | Responsabilidade |
|---|---|
| [[strategy.py]] | Geração de sinais técnicos (RSI-14, EMA-50/200, volume ratio, RS) |
| [[execution.py]] | Submissão de ordens BUY/SELL à API T212 |
| [[api_client.py]] | Cliente HTTP para a T212 API (demo e live) |
| [[exit_manager.py]] | Gestão de saídas — sistema de 3 barreiras |
| [[price_feed.py]] | Feed de preços em tempo real |
| [[feature_builder.py]] | Construção de features para modelos ML |

---

## Sinais de Entrada (Regras do Clyde)

```
Sinal A: RSI-14 < 40 (oversold) + EMA-50 > EMA-200 (tendência bull)
Sinal B: Volume ratio > 1.5x média 20d
Sinal C: RS (Relative Strength) > 1.0 vs SPY
Sinal D: Regime macro = BULL (via regime_detector.py)

BUY só se A + B + C + D simultaneamente → [[MOC_Bonnie]] aprova → [[execution.py]] executa
```

---

## Sistema de 3 Barreiras ([[exit_manager.py]])

| Barreira | Condição | Ação |
|---|---|---|
| Stop Loss | -5% | SELL imediato |
| Take Profit | +10% | SELL imediato |
| Trailing Stop | Pico - 3% | SELL se posição em lucro |

---

## Fluxo de Dados de Clyde

```
[[price_feed.py]] → preços raw
      ↓
[[feature_builder.py]] → RSI, EMA, volume ratio, RS
      ↓
[[strategy.py]] → sinal BUY/SELL/HOLD
      ↓
[[MOC_Bonnie]] → aprovação de risco
      ↓
[[execution.py]] → ordem via [[api_client.py]]
      ↓
[[exit_manager.py]] → monitorização contínua
```

---

## Backtesting e Análise

| Ficheiro | Papel |
|---|---|
| [[backtest.py]] | Backtesting individual de estratégias |
| [[mass_backtest.py]] | Análise estatística em massa |
| archive/backtest_comparison.py | Comparação OOS de 4 setups (legacy) |

---

## God Node Crítico

- **`compute_ema()`** — ponte entre [[MOC_Clyde]], [[MOC_CRO]], backtesting e [[MOC_Infraestrutura]]. Betweenness centrality 0.063 — nó mais crítico do sistema para dissecar.

---

## Ligações Cruzadas

- [[MOC_Bonnie]] — cada sinal de Clyde passa por Bonnie antes de ser executado
- [[MOC_CRO]] — regime macro condiciona todos os sinais de Clyde
- [[MOC_Infraestrutura]] — config.py define parâmetros RSI/EMA/ATR usados aqui
- [[FASE-1]] — roadmap de evolução dos sinais (RS filter, VIX macro)
