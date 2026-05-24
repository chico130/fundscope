# Diretrizes do FundScope

## Regra de Ouro de Infraestrutura (Poupança de Tokens)
1. Antes de responderes a qualquer questão sobre a arquitetura do robô, o fluxo entre Clyde/Bonnie/CRO, ou dependências de ficheiros, deves consultar estritamente o [[GRAPH_REPORT.md]] gerado pelo Graphify.
2. NÃO leias os ficheiros de código completos (como [[strategy.py]], [[cro.py]] ou [[bonnie.py]]) a menos que o utilizador te peça para alterar linhas de código específicas desses ficheiros. Confia na estrutura do grafo para entenderes as dependências.

## Comandos Úteis do Projeto

### Backtest / Stress-test
- **Backtest standard (4 variantes):** `PYTHONPATH=. python scripts/backtest.py --since 2019-01-01 --use-optimized`
- **Kelly comparison (ON vs OFF):** `PYTHONPATH=. python scripts/backtest.py --since 2019-01-01 --use-optimized --kelly`
- **Com Bonnie v3:** `PYTHONPATH=. python scripts/backtest.py --since 2024-05-23 --use-optimized --bonnie-v3`
- **Stress-test 7 anos:** `PYTHONPATH=. python scripts/backtest.py --since 2019-01-01 --until 2026-05-24 --capital 5000 --use-optimized`

### Learner
- **Learner 7 anos (60 ciclos):** `PYTHONPATH=. python bot/learner_backtest.py --cycles 60 --since 2019-01-01`
- **Learner rápido (10 ciclos, 2 anos):** `PYTHONPATH=. python bot/learner_backtest.py --cycles 10`

### Bonnie Retrain
- **Retrain v2 (padrão):** `PYTHONPATH=. python scripts/retrain_bonnie.py`
- **Retrain v3 (7 anos, labels calibradas):** `PYTHONPATH=. python scripts/retrain_bonnie.py --since 2017-01-01 --until 2026-05-01 --model-version v3`

### Pipeline / Outros
- **Execução Manual do Pipeline:** `python bot/phase0.py`
- **Atualização do Grafo de Conhecimento:** `/graphify .`
- **Validação de Sintaxe:** `python -c "import ast; ast.parse(open('bot/bonnie.py', encoding='utf-8').read())"`
- **Análise Estatística/Backtest:** `python -m bot.mass_backtest`

## Estado Atual — v3.1 (run-006, 2026-05-24)

### Parâmetros ativos (optimized_backtest_params.json)
- `atr_stop_mult_value`: 1.75 (era 3.0)
- `atr_tp_mult`: 4.25 (era 3.0)
- `value_trail_activation`: 3.0 (era 2.25)
- `value_trail_distance`: 3.5 (era 2.0)
- `max_position_pct`: 11.0% (era 10.0%)

### Modelo ativo
- **Bonnie v4** (`bonnie_model_v4.pkl`) — labels calibradas TP=4.25×ATR / SL=1.75×ATR
- BonnieML auto-carrega v4 por prioridade de ficheiro (v4 > v3 > v2)
- Thresholds: todos 0.30 per-regime (`bonnie_thresholds_v4.json`)
- v3 REJEITADA+APAGADA; v2 mantido como fallback

### Kelly
- Implementado (`_kelly_size_factor` em backtest.py + cro.py)
- **DESACTIVADO** — WR=37.6% incompatível com Quarter-Kelly
- `CRO_CONFIG["enable_kelly_sizing"] = False` (default permanente)

### Resultados de referência

**7yr Full (2019-2026, Bonnie v2):**
- **+224.5%** vs SPY +232.3% (alpha -7.8pp) | Sharpe 1.29 | DD -18.3%

**OOS (2024-01-01→2026-05-01, Bonnie v4) — run-006 (com label leakage):**
- +53.5% vs SPY +45.2% | Sharpe 1.94 | DD -9.6% | Bonnie filtra 32.6%

**OOS (2024-01-01→2026-05-01, Bonnie v4-clean) — REFERÊNCIA ACTIVA (run-007):**
- **+62.2% (+Bonnie)** vs SPY +45.2% | Alpha +17.0pp | Sharpe **2.09** | DD -10.8% | Calmar ~2.0 | filtra 34.9%
- WR 38% | R:R 2.5:1 | Profit Factor 1.73
- v4-clean substitui v4 como modelo activo (backup: bonnie_model_v4_orig.pkl)

### Próximos Passos — AGUARDAR 30 dias em produção (v4-clean)
- Sistema v3+B4-clean em monitorização real. **Nenhuma optimização adicional antes de validação real.**
- Métricas alvo após 30 dias: Sharpe ≥ 1.5, DD ≤ -15%, Bonnie filtra 25-40%

### Bonnie v5 — IDENTIFICADA, AGUARDA VALIDAÇÃO
- **NÃO CORRER** até validação real de 30 dias com v4-clean estar concluída
- Quando validar: aumentar `LABEL_HORIZON_DAYS` de 20 → ~57 dias (`ceil(4.25/1.5 × 20)`)
- Objectivo: aumentar label balance de 15.8% → ~30-40% (melhora F1 real — v4-clean tem F1=0.030)
- Comando: `PYTHONPATH=. python scripts/retrain_bonnie.py --since 2017-01-01 --until 2026-05-01 --model-version v4-clean --tp-mult 4.25 --sl-mult 1.75`
- Antes de correr: editar `LABEL_HORIZON_DAYS = 57` em retrain_bonnie.py (linha 68)

---
## Auto-Sync: 2026-05-24
- PC: DESKTOP-NGIATI2
- Runs recentes: run-004 (v3 Learner 7yr), run-005 (Kelly + Bonnie v3 rejeitada), run-006 (Bonnie v4 aceite), run-007 (v4-clean substitui v4, corrige data leakage)
- Learner: verificar data/beta/ para runs recentes
---
