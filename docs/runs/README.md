# Backtest / Optimização — Índice de Runs

Período de referência: **2024-05-23 → 2026-05-23** (2 anos)  
Capital inicial: **EUR 5,000**  
SPY benchmark: **+45.22%**, DD -18.76%, Sharpe 1.21, capital EUR 7,261

---

## Tabela de Runs

| Run | Data | Versão | Bots | Return | Sharpe | DD | Trades | Capital | Alpha |
|-----|------|--------|------|--------|--------|----|--------|---------|-------|
| [001](run-001.md) | 2026-05-23 | v1.0 | Clyde only | +29.8% | 1.50 | -5.6% | 677 | EUR 6,490 | -15.4pp |
| [002](run-002.md) | 2026-05-23 | v1.1 | Full (Bonnie v1) | +18.8% | 1.31 | -5.7% | 467 | EUR 5,939 | -26.4pp |
| [003](run-003.md) | 2026-05-23 | v2.0 | Full (Bonnie v2) | +43.4% | 1.35 | -11.9% | 817 | EUR 7,170 | -1.8pp |

---

## Melhor de cada categoria

| Categoria | Run | Valor |
|-----------|-----|-------|
| Maior return | [003](run-003.md) | +43.4% |
| Menor drawdown | [001](run-001.md) | -5.6% |
| Maior Sharpe | [001](run-001.md) | 1.50 |
| Mais trades | [003](run-003.md) | 817 |
| Menor alpha gap | [003](run-003.md) | -1.8pp |
| Capital final máximo | [003](run-003.md) | EUR 7,170 |

---

## Evolução dos critérios de sucesso

| Critério | Target | run-001 | run-002 | run-003 |
|----------|--------|---------|---------|---------|
| Trades ≥ 1200 | 1200 | ✗ 677 | ✗ 467 | ✗ 817 |
| Return ≥ 35% | 35% | ✗ 29.8% | ✗ 18.8% | ✓ 43.4% |
| Sharpe ≥ 1.2 | 1.2 | ✓ 1.50 | ✓ 1.31 | ✓ 1.35 |
| Max DD ≤ -15% | -15% | ✓ -5.6% | ✓ -5.7% | ✓ -11.9% |
| Alpha ≥ 0pp | 0pp | ✗ -15.4pp | ✗ -26.4pp | ✗ -1.8pp |
| **Total** | **5/5** | **2/5** | **2/5** | **3/5** |

---

## Nota sobre run-002

A run-002 é uma **regressão** vs run-001: adicionar Bonnie v1 com threshold 0.60 global piorou tanto o return (+18.8% vs +29.8%) como os trades (467 vs 677). Esta run serviu de diagnóstico para motivar o redesign v2.

---

## Como adicionar uma nova run

1. Copiar `RUN_TEMPLATE.md` para `run-NNN.md`
2. Preencher todos os campos com dados reais (sem arredondamentos, sem omissões)
3. Atualizar a tabela neste README
4. Commit: `docs(runs): add run-NNN — [título curto]`
