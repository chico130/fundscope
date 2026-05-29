# FundScope — Project Health Dashboard
> Auto-gerado por `scripts/generate_health_report.py` a cada ciclo. **NÃO editar à mão.**
> Última avaliação: 2026-05-29 18:49 UTC · ciclo `2026-05-28T22:52`

## 🔴 Score Geral: 40/100 (Nível D)
_▬ +0 vs ciclo anterior_

> ⚠️ **Score limitado** por `heartbeat`

_Bot `active` · regime `bull_lateral` · fase demo_

| Dimensão | Score | | Tendência |
|---|---|---|---|
| Performance | 51 | 🔴 | ▬ |
| Saúde Técnica | 40 | 🔴 | ▬ |
| Qualidade do Código | 62 | 🟡 | ▬ |
| Pontos Fortes | 100 | 🟢 | ▬ |
| Pontos Fracos ↑=melhor | 30 | 🔴 | ▬ |

### 📈 Performance — 51/100  _(confiança 5%, 1 trade(s) fechado(s))_
- P&L acumulado: `+0.16%` → 🔴 51
- Drawdown máximo: `-0.04%` → 🟢 100
- Expectancy: `+9.86€/trade` · win rate `100%` → 🟡 75
- Sharpe: `N/D`
- ⚠️ _amostra insuficiente (1 trade(s) fechado(s) de 20): score amortecido_
- ⚠️ _Sharpe indisponível_

### 🔧 Saúde Técnica — 40/100
- Heartbeat: `1196.6 min` (active) → 🔴 0
- Erros (7d): `8` total (0 críticos · 0 graves · 8 avisos) → 🟡 60
- Circuitos abertos: nenhum → 🟢 100
- Workflows: `N/D` (gh indisponível)
- ℹ️ _8 aviso(s) nos últimos 7 dias_
- ℹ️ _gh CLI indisponível — workflow_score N/D_
- ℹ️ _status.json pode estar desactualizado — considera git pull para reflectir ciclos recentes_

### 🧪 Qualidade do Código — 62/100
- Cobertura: `N/D` _(instalar pytest-cov e executar CI para medição real)_
- Módulos com teste: `12/32` (38%) → 🔴 38
- TODOs activos: `10` → 🟢 80
- Sintaxe `bot/`: `OK` → 🟢 100
- Módulos sem teste: `backtest` · `bonnie` · `circuit_breaker` · `evaluate_bonnie` · `feature_builder` · `gains_insights` · `learner` · `learner_backtest` _+12 mais_
- ℹ️ _pytest-cov não instalado ou cobertura não medida: a usar proxy test-presence_

## ✅ Pontos Fortes — 100/100
- **Drawdown** — `100/100` _performance_
- **APIs / circuit breakers** — `100/100` _technical health_
- **Sintaxe** — `100/100` _code quality_

## ⚠️ Pontos Fracos — 30/100 _(maior = melhor)_
- **Heartbeat** — `0/100` _technical health_
- **Test presence** — `38/100` _code quality_
- **P&L acumulado** — `51/100` _performance_

## 📊 Histórico (últimas 7 avaliações)
| Ciclo UTC | Geral | Perf | Técnica | Código | Fortes | Fracos |
|-----------|-------|------|---------|--------|--------|--------|
| 2026-05-29 12:08 | 40 | 51 | 40 | 41 | 100 | 17 |
| 2026-05-29 18:45 | 40 | 51 | 40 | 41 | 100 | 17 |
| 2026-05-29 18:47 | 40 | 51 | 40 | 62 | 100 | 30 |
| 2026-05-29 18:49 | 40 | 51 | 40 | 62 | 100 | 30 |

---
_Fontes: `account_metrics.json` · `beta_summary.json` · `status.json` · `logs/errors/` · `gh run list`_
