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

## Estado Atual — v3.1 (run-005, pós-análise 2026-05-24)

### Parâmetros ativos (optimized_backtest_params.json)
- `atr_stop_mult_value`: 1.75 (era 3.0)
- `atr_tp_mult`: 4.25 (era 3.0)
- `value_trail_activation`: 3.0 (era 2.25)
- `value_trail_distance`: 3.5 (era 2.0)
- `max_position_pct`: 11.0% (era 10.0%)

### Modelo ativo
- **Bonnie v2** (`bonnie_model_v2.pkl`) — único modelo disponível
- Bonnie v3 REJEITADA e `bonnie_model_v3.pkl` APAGADO (label mismatch: treino 1.5×ATR vs estratégia 4.25×ATR)
- BonnieML carrega v2 automaticamente (v3.pkl não existe)

### Kelly
- Implementado (`_kelly_size_factor` em backtest.py + cro.py)
- **DESACTIVADO** — WR=37.6% incompatível com Quarter-Kelly (f=2.9% → posições a 27% do máximo → retorno colapsa +224.5%→+65.6%)
- `CRO_CONFIG["enable_kelly_sizing"] = False` (default permanente até WR > 50%)

### Resultados de referência (7yr, Full, v3 params, Bonnie v2)
- **+224.5%** vs SPY +232.3% (alpha -7.8pp) | Sharpe 1.29 | DD -18.3%
- Confirmação OOS (2024-01-01→2026-05-01): **+39.6%** | Bonnie filtra 27.3% | v2 auto-carregado ✅

### Próximo passo — Bonnie v4
- Retreinar com labels calibradas para os params actuais: `TP_ATR_MULT=4.25`, `SL_ATR_MULT=1.75`
- Comando: `PYTHONPATH=. python scripts/retrain_bonnie.py --since 2017-01-01 --until 2026-05-01 --model-version v3`
- Antes de correr: actualizar `TP_ATR_MULT` e `SL_ATR_MULT` em retrain_bonnie.py (linhas 65-67)

---
## Auto-Sync: 2026-05-24
- PC: DESKTOP-NGIATI2
- Ultimo commit: d205750 - feat(backtest+learner): v2 evoluído — trailing, cap 10%, Bonnie v2, Learner + docs runs
- Runs recentes: run-004 (v3 Learner 7yr), run-005 (Kelly + Bonnie v3 rejeitada)
- Learner: verificar data/beta/ para runs recentes
---
