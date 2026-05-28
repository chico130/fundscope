---
id: template-run
title: "Template — Run de Backtest"
type: template
domain: geral
regime: n/a
tags: [template, run, backtest]
links_obrigatorios:
  parent_moc: "[[MOC_FundScope]]"
  vizinhos: "[[runs-index]]"
status: stable
ultima_revisao: 2026-05-24
---

# Run NNN — [Título Curto]

**Data:** YYYY-MM-DD  
**Versão:** vX.Y  
**Período:** YYYY-MM-DD → YYYY-MM-DD  
**Capital inicial:** EUR X,XXX  

---

## Configuração

| Parâmetro | Valor |
|-----------|-------|
| Bots ativos | Clyde / Bonnie / CRO |
| Bonnie ML | v1 / v2 / desativado |
| Earnings gate | sim / não |
| RS bullish filter | sim / não |
| Value trailing stop | sim / não |
| Same-ticker adds | sim / não |
| `max_position_pct` | X% |
| `atr_stop_mult_value` | X.X |
| `atr_stop_mult_momentum` | X.X |
| `atr_tp_mult` | X.X |
| `value_trail_activation` | X.XX |
| `value_trail_distance` | X.X |
| `bonnie_threshold` | X.XX |

---

## Resultados

| Métrica | Valor |
|---------|-------|
| Return total | +X.X% |
| Return anual | +X.X% |
| Max drawdown | -X.X% |
| Sharpe (anual) | X.XX |
| Calmar | X.XX |
| Profit factor | X.XX |
| Win rate | X.X% |
| Trades | X |
| Capital final | EUR X,XXX |
| Deployed médio | X.X% |
| N adds | X |

### vs SPY (mesmo período)

| | Esta run | SPY |
|--|----------|-----|
| Return | +X.X% | +X.X% |
| Max DD | -X.X% | -X.X% |
| Sharpe | X.XX | X.XX |
| Capital final | EUR X,XXX | EUR X,XXX |
| Alpha | **±X.Xpp** | — |

---

## Variantes testadas (se aplicável)

| Variante | Return | Sharpe | DD | Trades | Capital |
|----------|--------|--------|----|--------|---------|
| Clyde-only | | | | | |
| +Bonnie | | | | | |
| +Earnings | | | | | |
| Full | | | | | |

---

## Parâmetros otimizados (Learner)

```
não aplicável / ciclo N, fitness X.XXXX
```

Parâmetros alterados vs defaults:

| Parâmetro | Default | Otimizado |
|-----------|---------|-----------|
| | | |

---

## Alterações vs run anterior

- 

---

## Critérios de sucesso

| Critério | Target | Resultado | Estado |
|----------|--------|-----------|--------|
| Trades | ≥ 1200 | X | ✓ / ✗ |
| Return total | ≥ 35% | X% | ✓ / ✗ |
| Sharpe | ≥ 1.2 | X.XX | ✓ / ✗ |
| Max DD | ≤ -15% | -X.X% | ✓ / ✗ |
| Alpha vs SPY | ≥ 0pp | ±X.Xpp | ✓ / ✗ |

---

## Notas / Observações

- 
