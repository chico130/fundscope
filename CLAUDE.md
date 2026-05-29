---
id: claude-diretrizes
title: "Diretrizes do FundScope"
type: spec
domain: geral
regime: n/a
tags: [diretrizes, claude-code, arquitetura, regras]
links_obrigatorios:
  parent_moc: "[[MOC_FundScope]]"
  vizinhos: "[[README]] [[000-INDEX]] [[CRO_SPEC]]"
status: stable
ultima_revisao: 2026-05-28
---

# FundScope â€” Guia de Arquitectura para Claude Code

> **Le este ficheiro no inicio de cada sessao.** Contem todo o contexto necessario sem explorar o repo.
> As seccoes "Estado Actual", "Ultimas Alteracoes" e "O Que Ja Existe" sao auto-actualizadas por `scripts/update_claude_md.py` apos cada ciclo.
>
> **ANTES DE ALTERAR QUALQUER CODIGO: ler `MEMORY_ERRORS.md` na raiz.** Politica de zero erros repetidos â€” nao reintroduzir nenhum erro ja documentado e registar cada nova fix.

---

## O QUE JÃ EXISTE

<!-- O-QUE-JA-EXISTE-START -->
### Implementado e a funcionar
- Ciclo principal phase0 (orquestrador, 15min via GitHub Actions)
- Clyde: sinais RSI-14, EMA-50/200, volume ratio, ATR
- Bonnie v4-clean: filtro ML (threshold 0.30 por regime, fail-open)
- CRO: sizing por regime (bull_trending=1.0x, bull_lateral=0.5x, bear=0.0x)
- Executor: BUY via POST + SELL via DELETE na T212 demo API
- Exit Manager: Three Barriers (TP atr_tp_mult=4.25, SL atr_stop_mult=1.75, trailing ATR)
- Regime Detector: 4 regimes via SPY EMA-200 + breadth + ATR (cache regime.json)
- Watchlist Manager: scoring momentum1M(40%)+3M(30%)+liquidez(20%)+qualidade(10%)
- Throttler: distribui fetches de watchlist entre ciclos (cursor persistente)
- Watchdog: quarentena + EMERGENCY_LOCK.txt + commit + Telegram SOS
- Learner: analise de trades fechados + bonnie_log.json (corre no fim do ciclo)
- Notifier: Telegram imediato apos cada trade (enviar_trade_executada)
- Frontend GitHub Pages: dashboard SPA read-only (sem calculos no browser)
- Rate limiter centralizado com alertas Telegram (bot/throttler.py)
- Workflow health check diario (data/beta/status.json)
- Workflow emergency run manual (.github/workflows/)
- Data Layer: get_full_portfolio_state() como unica source of truth
- Logger estruturado JSON (logs/trades/ + logs/errors/)
- Ingest: update_portfolio.py + update_prices.py (workflows separados)

### Em desenvolvimento
- Validacao real 30 dias de Bonnie v4-clean (iniciada ~2026-05-24, termina ~2026-06-24)

### Planeado (nao iniciado)
- Live trading em conta real (aguarda validacao demo de 30 dias concluida)
- Bonnie v5: LABEL_HORIZON_DAYS 20->57 (identificada e bloqueada ate validacao real)

**Ultimo trade executado:** `ARM` em `2026-05-22T14:00`
**Ultimo ciclo:** `2026-05-29T19:18Z` | status: `active` | regime: `bull_trending`
<!-- O-QUE-JA-EXISTE-END -->

---

## 1. Arquitectura â€” Fluxo de Execucao

O bot corre a cada 15 minutos via GitHub Actions (13:00-21:00 UTC, dias uteis, seg-sex).

```
Mercado (Finnhub + yfinance)
        |
phase0.py::run()            <- orquestrador principal (1396 linhas)
        |
        +- _get_regime_safe()          -> regime_detector.get_current_regime()
        +- _get_watchlist_safe()       -> watchlist_manager.build_watchlist()
        +- get_full_portfolio_state()  -> data_layer (T212 resync obrigatorio)
        +- exit_manager.check_exit_barriers()      <- trailing stop / TP / SL
        +- _check_momentum_exits()                 <- ATR trailing para MOMENTUM
        +- _scan_watchlist_candidates()
        |       +- strategy.generate_signals()     <- Clyde: RSI+EMA+volume+ATR
        +- _apply_bonnie_filter()   -> bonnie.filter_proposals()   <- ML gate
        +- _apply_social_veto()     -> social_sentiment.json       <- Reddit veto
        +- cro.CRO.observe() + interpret()  <- risk_factor + regime multiplier
        +- _execute_phase1()  [se PHASE1_EXECUTION=True]
               +- execution.execute_exit()        <- SELL via T212 DELETE
               +- execution.execute_trade()       <- BUY via T212 POST
               +- notifier.enviar_trade_executada() <- Telegram imediato
```

**Cadeia de comando:** Clyde propoe -> Bonnie audita -> CRO dita alocacao -> Executor submete.

**Regimes:** `bull_trending` (1.0x) | `bull_lateral` (0.5x) | `bear_correction` (0.0x) | `bear_capitulation` (0.0x)

---

## 2. Componentes e Responsabilidades

| Ficheiro | Componente | Responsabilidade | Atencao ao editar |
|---|---|---|---|
| `bot/phase0.py` | Orquestrador | Ciclo principal, coordena todos os modulos | Ordem de chamadas e intencional; `PHASE1_EXECUTION` liga/desliga execucao real |
| `bot/config.py` | Config central | `RISK_CONFIG`, `CRO_CONFIG`, `PHASE1_EXECUTION`, `LIVE_TRADING` | `LIVE_TRADING=False` permanente; alterar `CRO_CONFIG` afecta sizing de todas as ordens |
| `bot/strategy.py` | Clyde | Sinais RSI-14, EMA-50/200, volume ratio, ATR | `ProposedTrade` e o DTO central; `generate_signals()` -> `propose_trades()` |
| `bot/bonnie.py` | Bonnie ML | `filter_proposals()` â€” aprova/veta cada trade | Fail-open por design; carrega v4 > v3 > v2 por prioridade de ficheiro |
| `bot/cro.py` | CRO | `observe()` -> `interpret()` -> `speak()` | `Verdict.risk_factor` multiplica size de todas as ordens |
| `bot/execution.py` | Executor | `execute_trade()`, `execute_exit()` | SELL via DELETE (nao POST); log duplo: `diario_trades.json` + `data/beta/beta_trades.json` |
| `bot/data_layer.py` | Data Layer | `get_full_portfolio_state()`, `enrich_with_technicals()` | T212 sync e oportunista (`_try_t212_sync`); Finnhub/yfinance para precos |
| `bot/exit_manager.py` | Exit Manager | `check_exit_barriers()` â€” Three Barriers (TP/SL/Trailing) | Le `beta_trades.json`; patches atomicos de barrier fields |
| `bot/notifier.py` | Whisper | Telegram: `enviar_trade_executada()`, `enviar_oportunidade()` | Sempre em `try/except` isolado â€” falha nunca aborta o ciclo |
| `bot/learner.py` | Learner | `run_learner_cycle()` â€” analise de trades fechados | Corre silenciosamente no fim do ciclo; escreve `bonnie_log.json` |
| `bot/regime_detector.py` | Regime | SPY vs EMA-200, breadth, ATR â€” 4 regimes | Cache em `data/beta/regime.json`; fallback conservador `bull_lateral` |
| `bot/watchlist_manager.py` | Watchlist | Seleccao e scoring de candidatos (max 100) | Score: momentum1M(40%) + 3M(30%) + liquidez(20%) + qualidade(10%) |
| `bot/position_ledger.py` | Ledger | Cache local de posicoes (espelho T212) | T212 API ganha sempre em divergencia; `positions_ledger.json` |
| `bot/api_client.py` | API Client | HTTP calls a T212 demo API | Rate limit ~1 req/s; `reconcile_orphan_buy_orders()` cancela BUYs duplicados |
| `bot/market_hours.py` | Market Hours | `is_market_open()` â€” NYSE hours + DST | Gate de entradas em `phase0.py` |
| `bot/logger.py` | Logger | `log_decision()`, `log_error()` | JSON estruturado: `logs/trades/` + `logs/errors/` |
| `bot/throttler.py` | Throttler | `WatchlistThrottler` â€” distribui fetches ao longo do ciclo | Cursor persiste entre ciclos em `throttler_state.json` |
| `bot/watchdog.py` | Watchdog | `check_quarantine_and_abort()`, `quarantine()` | EMERGENCY_LOCK.txt -> git commit -> Telegram SOS |
| `ingest/update_portfolio.py` | Ingest | Sincroniza `portfolio.json` (raiz) + `data/beta/` | Corre no fim do ciclo principal + workflow separado |
| `ingest/update_prices.py` | Ingest | Actualiza `data.json` via yfinance | Workflow diario pos-fecho US |
| `serve.py` | Dev Server | HTTP local com autenticacao e cache JSON RAM | `_JSON_CACHE` invalida a cada 60s |

---

## 3. JSONs â€” Pipeline de Dados

```
T212 API â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€+
Finnhub / yfinance â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€+
                                 v
       Python agents (bot/ + ingest/)
                                 |
         +â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€+â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€+
         v                       v                    v
   data/beta/               raiz/                 logs/
   portfolio.json           portfolio.json        trades/YYYY-MM-DD.json
   beta_trades.json         data.json             errors/YYYY-MM-DD.json
   beta_positions.json      markets.json          bonnie_log.json
   beta_equity.json         news.json
   beta_analysis.json       earnings.json
   cro_insights.json        diario_trades.json
   status.json              config_risco.json
                                 |
                                 v
               Frontend (GitHub Pages â€” SPA)
               â€” apenas le, nunca calcula â€”
```

**JSONs criticos:**

| JSON | Quem escreve | Conteudo |
|---|---|---|
| `data/beta/status.json` | `phase0.py` fim de cada ciclo | Heartbeat: `last_check`, `bot_status`, `regime`, `mode` |
| `data/beta/beta_trades.json` | `execution.py` + `reporter.py` | Historico de trades (abertos + fechados) |
| `data/beta/beta_analysis.json` | `phase0.py` | Resultado completo do ciclo (oportunidades, sinais, skips) |
| `data/beta/portfolio.json` | `data_layer.py` + `ingest/update_portfolio.py` | Estado do portfolio T212 |
| `data/beta/cro_insights.json` | `cro.speak()` | Narrativa do CRO e metricas de risco |
| `config_risco.json` (raiz) | Manual ou Bonnie | `permite_comprar`, `tamanho_maximo_posicao` â€” gate de risco |
| `data/beta/optimized_backtest_params.json` | `scripts/backtest.py` | Parametros activos (run-007) â€” nao editar manualmente |

---

## 4. Workflows GitHub Actions

| Workflow | Cron | O que faz |
|---|---|---|
| `run-trading-bot.yml` | `*/15 13-20 * * 1-5` + `0 21 * * 1-5` | Ciclo principal (Clyde + Bonnie + CRO + Execution + Reporter) |
| `update-portfolio.yml` | separado | Sync `portfolio.json` e `symbol_cache.json` |
| `update-prices.yml` | diario pos-fecho | Actualiza `data.json` via yfinance |
| `update-markets.yml` | separado | Actualiza `markets.json` |
| `update-news.yml` | separado | Actualiza `news.json` via marketaux/newsapi |
| `pages.yml` | push para main | Deploy GitHub Pages |

Secrets: `T212_API_ID`, `T212_API_KEY`, `FINNHUB_TOKEN`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `GEMINI_API_KEY`.

---

## 5. Estado Actual

<!-- ESTADO-ACTUAL-START -->
**Actualizado em:** 2026-05-29 19:19 UTC

- **Bot status:** `active` | Ultimo ciclo: `2026-05-29T19:18Z`
- **Regime:** `bull_trending` | Modo: `phase1_auto`
- **Posicoes abertas:** 1 | **Trades abertos:** 0 | **Trades hoje:** 0
- **Fase:** Fase 1 — execucao automatica em conta demo (`PHASE1_EXECUTION=True`, `LIVE_TRADING=False`)
- **Modelo activo:** Bonnie v4-clean (`bonnie_model_v4.pkl`) — thresholds 0.30 por regime
- **Parametros:** `atr_stop_mult=1.75` | `atr_tp_mult=4.25` | `max_position_pct=11%`
- **OOS ref (run-007):** +62.2% vs SPY +45.2% | Alpha +17pp | Sharpe 2.09 | DD -10.8% | WR 38% | R:R 2.5:1
- **Proximo passo:** Aguardar 30 dias de validacao real com v4-clean. **Sem optimizacoes adicionais.**
<!-- ESTADO-ACTUAL-END -->

---

## 6. Ultimas Alteracoes

<!-- ULTIMAS-ALTERACOES-START -->
| Data | Hash | Descricao |
|---|---|---|
| 2026-05-29 | `67549e1` | chore: update markets.json [skip ci] |
| 2026-05-29 | `0cfdd85` | chore: update news.json [skip ci] |
| 2026-05-29 | `7dd47c3` | chore: update markets.json [skip ci] |
| 2026-05-29 | `0e80b27` | chore: update news.json [skip ci] |
| 2026-05-29 | `0fbf5e7` | 2905 |
| 2026-05-29 | `feff0e0` | feat: realised gains tab with Gemini insights (9-week retention) |
| 2026-05-29 | `47dba92` | feat: automated project health dashboard |
| 2026-05-29 | `4aa1b3e` | chore: update portfolio [skip ci] |
| 2026-05-29 | `965db8a` | fix: verify t212-debug workflow references correct script path |
| 2026-05-29 | `d8a8ded` | fix: use centralised daily_flags for CRO speak deduplication |
<!-- ULTIMAS-ALTERACOES-END -->

---

## 7. Regras de Edicao

### Pre-requisito obrigatorio
- **Ler `MEMORY_ERRORS.md` (raiz) ANTES de alterar codigo.** Confirmar que nenhuma alteracao reintroduz um erro ja documentado; apos qualquer fix nova, adicionar entrada (data, sintoma, causa, solucao).

### Convencoes de codigo
- Python 3.11, `from __future__ import annotations`, type hints onde pratico
- Escrita de ficheiros sempre atomica: `.tmp` -> `rename` (padrao estabelecido em todo o codebase)
- I/O lateral (Telegram, logs, ficheiros auxiliares) sempre em `try/except` isolado, sem `raise`
- Timestamps sempre UTC com sufixo `Z`: `datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")`
- `flush=True` em todos os `print()` do ciclo principal (sem ele, GitHub Actions perde ordem cronologica)
- Sem comentarios obvios; comentar apenas quando o WHY e nao-obvio para o leitor

### Formato de commits
`type: descricao curta` â€” tipos: `feat`, `fix`, `refactor`, `docs`, `chore`, `security`, `bot`

### O que nunca tocar sem confirmacao explicita do utilizador
- `LIVE_TRADING = True` em `config.py` â€” permanece `False` ate decisao deliberada
- `PHASE1_EXECUTION` â€” mudar isto liga/desliga execucao de ordens reais na conta demo
- `bonnie_model_v4.pkl` e `bonnie_model_v4_orig.pkl` â€” modelos activos; nao apagar
- `data/beta/optimized_backtest_params.json` â€” parametros calibrados do run-007
- Qualquer logica de risco em `cro.py` sem rever primeiro `vault/specs/CRO_SPEC.md`
- Frontend HTML/JS: nao adicionar calculos de estado (Regra de Ouro â€” ver R6)
- `cancel-in-progress: false` nos workflows â€” intencional, nunca cancelar ciclo a meio

### Antes de editar ficheiros frequentes
- **`phase0.py`** â€” ler `_execute_phase1()` e `run()` completos antes de alterar; a ordem de chamadas e intencional
- **`execution.py`** â€” SELL usa `api_client.cancel_order_demo()` (DELETE), nao POST; confirmar schema T212
- **`config.py`** â€” qualquer alteracao ao `CRO_CONFIG` afecta sizing de todas as ordens; correr backtest depois
- **`bonnie.py`** â€” fail-open por design; qualquer mudanca ao threshold afecta o filtro em producao imediatamente
- **`data_layer.py`** â€” `get_full_portfolio_state()` e a unica fonte de verdade; nao introduzir calculos locais

---

## 8. Regras Nao-Negociaveis (Invariantes de Arquitectura)

### R1 â€” T212 API e a unica Source of Truth
- **PROIBIDO** calcular portfolio/posicoes localmente apos um trade
- **PROIBIDO** usar `position_ledger.json` como verdade absoluta no frontend
- Apos qualquer `execute_trade()`/`execute_exit()`: chamar `get_full_portfolio_state()` imediatamente
- Se ledger local divergir da T212 API: **API ganha sempre**; logar em `data/beta/sync_warnings.json`

### R2 â€” Heartbeat LED (status.json)
`data/beta/status.json` escrito no **fim de cada ciclo** (sucesso ou erro controlado):
```json
{"last_check": "2026-05-28T14:30:00Z", "bot_status": "active", "regime": "bull_trending", "mode": "phase1_auto"}
```
- Verde no site = `now() - last_check < 15min` **e** `bot_status == "active"`
- Em erro fatal: escrever `"bot_status": "error"` num `try/finally` no topo do ciclo

### R3 â€” Falhas Nao-Cascateantes
Todo o I/O lateral em `try/except` isolado. Hierarquia de prioridade:
1. **Execucao T212** â€” falha aborta o trade
2. **Resync T212** â€” falha marca ciclo como degradado, nao aborta
3. **Persistencia local** â€” falha e loggada, nao aborta
4. **Telegram** â€” falha e loggada, **nunca** aborta

### R4 â€” Notificacoes Imediatas
`notifier.enviar_trade_executada(result, modo)` chamado **dentro** de `execute_trade()`/`execute_exit()`, apos confirmacao T212, antes do `return`. Nunca acumular para enviar no fim do ciclo.

### R5 â€” Timestamps no Stdout
`phase0.py` imprime com `flush=True`:
```python
print(f"[{_ts()}] === FundScope phase0 START ===", flush=True)
print(f"[{_ts()}] === FundScope phase0 END === {dur} | signals={n} | executed={n}", flush=True)
```
Pontos obrigatorios: START, apos T212 sync, antes/apos cada ordem, END com resumo.

### R6 â€” Frontend So Le (Regra de Ouro)
O frontend (GitHub Pages) **nunca calcula estado**. Apenas le JSONs gerados pelos agentes Python.
Aplica-se a: valor do portfolio, P&L, posicoes, cash disponivel, qualquer metrica derivada.

---

## Checklist de Code Review (qualquer PR ao bot)

- [ ] Nenhum calculo de portfolio/posicao localmente apos trade â€” so resync via T212 API
- [ ] `data/beta/status.json` actualizado no fim do ciclo
- [ ] Todo o I/O lateral (Telegram, ficheiros, webhooks) em `try/except` isolado sem `raise`
- [ ] `enviar_trade_executada()` chamado imediatamente apos confirmacao T212
- [ ] `phase0.py` tem `print(..., flush=True)` com timestamp no inicio e fim do ciclo
- [ ] Frontend HTML/JS nao foi modificado para calcular estado

---

## Comandos Uteis do Projecto

```bash
# Ciclo manual
PYTHONPATH=. python -m bot.phase0

# Backtest standard (4 variantes, parametros optimizados)
PYTHONPATH=. python scripts/backtest.py --since 2019-01-01 --use-optimized

# Backtest OOS com Bonnie v4-clean (referencia actual)
PYTHONPATH=. python scripts/backtest.py --since 2024-01-01 --use-optimized

# Stress-test 7 anos
PYTHONPATH=. python scripts/backtest.py --since 2019-01-01 --until 2026-05-24 --capital 5000 --use-optimized

# Learner 7 anos (60 ciclos)
PYTHONPATH=. python bot/learner_backtest.py --cycles 60 --since 2019-01-01

# Retrain Bonnie v4-clean (NAO CORRER antes de validacao real concluida)
PYTHONPATH=. python scripts/retrain_bonnie.py --since 2017-01-01 --until 2026-05-01 --model-version v4-clean --tp-mult 4.25 --sl-mult 1.75

# Update CLAUDE.md manualmente
python scripts/update_claude_md.py

# Update grafo de conhecimento
# /graphify .  (via Skill tool no Claude Code)

# Validacao de sintaxe
python -c "import ast; ast.parse(open('bot/bonnie.py', encoding='utf-8').read())"

# Servir o dashboard localmente
python serve.py

# Analise em massa da Bonnie
PYTHONPATH=. python -m bot.mass_backtest
```

---

## Regra de Infra (Poupanca de Tokens)

1. Para questoes de arquitectura/fluxo/dependencias: consultar `graphify-out/GRAPH_REPORT.md` primeiro
2. Nao ler ficheiros de codigo completos a menos que va alterar linhas especificas neles
3. Confiar na estrutura do grafo para entender dependencias; so abrir ficheiros para edicao

---

## Historico de Decisoes (nao alterar automaticamente)

| Data | Decisao | Motivo |
|---|---|---|
| 2026-05-24 | Bonnie v4-clean activa (run-007) | Label leakage eliminado; OOS +62.2% vs SPY +45.2% |
| 2026-05-24 | Kelly desactivado permanentemente | WR=37.6% incompativel com Quarter-Kelly |
| 2026-05-24 | atr_stop_mult=1.75, atr_tp_mult=4.25 | Optimizacao run-006 (v3 params activos) |
| 2026-05-24 | max_position_pct=11% | Subida de 10% pos-optimizacao |
| 2026-05-28 | Bonnie v5 identificada mas bloqueada | Aguarda 30 dias de validacao real; LABEL_HORIZON_DAYS 20->57 |

---
## Auto-Sync: 2026-05-29 22:12
- PC: DESKTOP-0514V9J
- Ultimo commit: 0feef56 - fix: HTTP error handling for T212 API responses
- Learner: verificar data/beta/ para runs recentes
---