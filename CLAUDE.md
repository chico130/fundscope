# Diretrizes do FundScope

## Regra de Ouro de Infraestrutura (PoupanÃ§a de Tokens)
1. Antes de responderes a qualquer questÃ£o sobre a arquitetura do robÃ´, o fluxo entre Clyde/Bonnie/CRO, ou dependÃªncias de ficheiros, deves consultar estritamente o [[GRAPH_REPORT.md]] gerado pelo Graphify.
2. NÃƒO leias os ficheiros de cÃ³digo completos (como [[strategy.py]], [[cro.py]] ou [[bonnie.py]]) a menos que o utilizador te peÃ§a para alterar linhas de cÃ³digo especÃ­ficas desses ficheiros. Confia na estrutura do grafo para entenderes as dependÃªncias.

## Comandos Ãšteis do Projeto

### Backtest / Stress-test
- **Backtest standard (4 variantes):** `PYTHONPATH=. python scripts/backtest.py --since 2019-01-01 --use-optimized`
- **Kelly comparison (ON vs OFF):** `PYTHONPATH=. python scripts/backtest.py --since 2019-01-01 --use-optimized --kelly`
- **Com Bonnie v3:** `PYTHONPATH=. python scripts/backtest.py --since 2024-05-23 --use-optimized --bonnie-v3`
- **Stress-test 7 anos:** `PYTHONPATH=. python scripts/backtest.py --since 2019-01-01 --until 2026-05-24 --capital 5000 --use-optimized`

### Learner
- **Learner 7 anos (60 ciclos):** `PYTHONPATH=. python bot/learner_backtest.py --cycles 60 --since 2019-01-01`
- **Learner rÃ¡pido (10 ciclos, 2 anos):** `PYTHONPATH=. python bot/learner_backtest.py --cycles 10`

### Bonnie Retrain
- **Retrain v2 (padrÃ£o):** `PYTHONPATH=. python scripts/retrain_bonnie.py`
- **Retrain v3 (7 anos, labels calibradas):** `PYTHONPATH=. python scripts/retrain_bonnie.py --since 2017-01-01 --until 2026-05-01 --model-version v3`

### Pipeline / Outros
- **ExecuÃ§Ã£o Manual do Pipeline:** `python bot/phase0.py`
- **AtualizaÃ§Ã£o do Grafo de Conhecimento:** `/graphify .`
- **ValidaÃ§Ã£o de Sintaxe:** `python -c "import ast; ast.parse(open('bot/bonnie.py', encoding='utf-8').read())"`
- **AnÃ¡lise EstatÃ­stica/Backtest:** `python -m bot.mass_backtest`

## Estado Atual â€” v3.1 (run-006, 2026-05-24)

### ParÃ¢metros ativos (optimized_backtest_params.json)
- `atr_stop_mult_value`: 1.75 (era 3.0)
- `atr_tp_mult`: 4.25 (era 3.0)
- `value_trail_activation`: 3.0 (era 2.25)
- `value_trail_distance`: 3.5 (era 2.0)
- `max_position_pct`: 11.0% (era 10.0%)

### Modelo ativo
- **Bonnie v4** (`bonnie_model_v4.pkl`) â€” labels calibradas TP=4.25Ã—ATR / SL=1.75Ã—ATR
- BonnieML auto-carrega v4 por prioridade de ficheiro (v4 > v3 > v2)
- Thresholds: todos 0.30 per-regime (`bonnie_thresholds_v4.json`)
- v3 REJEITADA+APAGADA; v2 mantido como fallback

### Kelly
- Implementado (`_kelly_size_factor` em backtest.py + cro.py)
- **DESACTIVADO** â€” WR=37.6% incompatÃ­vel com Quarter-Kelly
- `CRO_CONFIG["enable_kelly_sizing"] = False` (default permanente)

### Resultados de referÃªncia

**7yr Full (2019-2026, Bonnie v2):**
- **+224.5%** vs SPY +232.3% (alpha -7.8pp) | Sharpe 1.29 | DD -18.3%

**OOS (2024-01-01â†’2026-05-01, Bonnie v4) â€” run-006 (com label leakage):**
- +53.5% vs SPY +45.2% | Sharpe 1.94 | DD -9.6% | Bonnie filtra 32.6%

**OOS (2024-01-01â†’2026-05-01, Bonnie v4-clean) â€” REFERÃŠNCIA ACTIVA (run-007):**
- **+62.2% (+Bonnie)** vs SPY +45.2% | Alpha +17.0pp | Sharpe **2.09** | DD -10.8% | Calmar ~2.0 | filtra 34.9%
- WR 38% | R:R 2.5:1 | Profit Factor 1.73
- v4-clean substitui v4 como modelo activo (backup: bonnie_model_v4_orig.pkl)

### PrÃ³ximos Passos â€” AGUARDAR 30 dias em produÃ§Ã£o (v4-clean)
- Sistema v3+B4-clean em monitorizaÃ§Ã£o real. **Nenhuma optimizaÃ§Ã£o adicional antes de validaÃ§Ã£o real.**
- MÃ©tricas alvo apÃ³s 30 dias: Sharpe â‰¥ 1.5, DD â‰¤ -15%, Bonnie filtra 25-40%

### Bonnie v5 â€” IDENTIFICADA, AGUARDA VALIDAÃ‡ÃƒO
- **NÃƒO CORRER** atÃ© validaÃ§Ã£o real de 30 dias com v4-clean estar concluÃ­da
- Quando validar: aumentar `LABEL_HORIZON_DAYS` de 20 â†’ ~57 dias (`ceil(4.25/1.5 Ã— 20)`)
- Objectivo: aumentar label balance de 15.8% â†’ ~30-40% (melhora F1 real â€” v4-clean tem F1=0.030)
- Comando: `PYTHONPATH=. python scripts/retrain_bonnie.py --since 2017-01-01 --until 2026-05-01 --model-version v4-clean --tp-mult 4.25 --sl-mult 1.75`
- Antes de correr: editar `LABEL_HORIZON_DAYS = 57` em retrain_bonnie.py (linha 68)

---
## Auto-Sync: 2026-05-25 20:26
- PC: DESKTOP-0514V9J
- Ultimo commit: a573ddf - fix: limpar beta_trades e diario — remover registos ARM SELL duplicados (Bug 1)
- Learner: verificar data/beta/ para runs recentes
---
