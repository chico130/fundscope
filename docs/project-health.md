# FundScope — Project Health Dashboard
> Auto-gerado por `scripts/generate_health_report.py` a cada ciclo. **NÃO editar à mão.**
> Última avaliação: 2026-05-29 12:08 UTC · ciclo `2026-05-28T22:52`

## 🔴 Score Geral: 40/100 (Nível D)

> ⚠️ **Score limitado** por `heartbeat`

_Bot `active` · regime `bull_lateral` · fase demo_

| Dimensão | Score | | Tendência |
|---|---|---|---|
| Performance | 51 | 🔴 | ▬ |
| Saúde Técnica | 40 | 🔴 | ▬ |
| Qualidade do Código | 41 | 🔴 | ▬ |
| Pontos Fortes | 100 | 🟢 | ▬ |
| Pontos Fracos ↑=melhor | 17 | 🔴 | ▬ |

### 📈 Performance — 51/100  _(confiança 5%, 1 trade(s) fechado(s))_
- P&L acumulado: `+0.16%` → 🔴 51
- Drawdown máximo: `-0.04%` → 🟢 100
- Expectancy: `+9.86€/trade` · win rate `100%` → 🟡 75
- Sharpe: `N/D`
- ⚠️ _amostra insuficiente (1 trade(s) fechado(s) de 20): score amortecido_
- ⚠️ _Sharpe indisponível_

### 🔧 Saúde Técnica — 40/100
- Heartbeat: `795.7 min` (active) → 🔴 0
- Erros (7d): `8` total (0 críticos · 0 graves · 8 avisos) → 🟡 60
- Circuitos abertos: nenhum → 🟢 100
- Workflows: `N/D` (gh indisponível)
- ℹ️ _8 aviso(s) nos últimos 7 dias_
- ℹ️ _gh CLI indisponível — workflow_score N/D_

### 🧪 Qualidade do Código — 41/100
- Cobertura: `N/D` _(instalar pytest-cov e executar CI para medição real)_
- Módulos com teste: `0/31` (0%) → 🔴 0
- TODOs activos: `10` → 🟢 80
- Sintaxe `bot/`: `OK` → 🟢 100
- Módulos sem teste: `api_client` · `backtest` · `bonnie` · `circuit_breaker` · `config` · `cro` · `data_layer` · `evaluate_bonnie` _+23 mais_
- ℹ️ _pytest-cov não instalado ou cobertura não medida: a usar proxy test-presence_
- ℹ️ _31 módulos sem teste: api_client, backtest, bonnie, circuit_breaker, config, cro +25 mais_

## ✅ Pontos Fortes — 100/100
- **Drawdown** — `100/100` _performance_
- **APIs / circuit breakers** — `100/100` _technical health_
- **Sintaxe** — `100/100` _code quality_

## ⚠️ Pontos Fracos — 17/100 _(maior = melhor)_
- **Heartbeat** — `0/100` _technical health_
- **Test presence** — `0/100` _code quality_
- **P&L acumulado** — `51/100` _performance_

## 📊 Histórico (últimas 7 avaliações)
| Ciclo UTC | Geral | Perf | Técnica | Código | Fortes | Fracos |
|-----------|-------|------|---------|--------|--------|--------|
| 2026-05-29 12:08 | 40 | 51 | 40 | 41 | 100 | 17 |

---
_Fontes: `account_metrics.json` · `beta_summary.json` · `status.json` · `logs/errors/` · `gh run list`_
