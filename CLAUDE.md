---
## ⚠️ LEITURA OBRIGATÓRIA ANTES DE EDITAR CÓDIGO

Antes de fazer qualquer alteração a este repositório, lê o ficheiro MEMORY_ERRORS.md na raiz.
Contém erros já conhecidos e resolvidos — não os reintroduza.
Regra: se a tua alteração toca em sw.js, qualquer HTML, ou bot/api_client.py, verifica primeiro o MEMORY_ERRORS.md.
---

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
ultima_revisao: 2026-05-31
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
**Ultimo ciclo:** `2026-05-31T18:10Z` | status: `active` | regime: `bull_trending`
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
| `bot/auditor.py` | Agente Auditor | Deteccao de padroes semanais + relatorio Telegram (sabados 06:00 UTC) | NUNCA escreve em `config_risco.json`; `param_suggestions[].auto_apply` sempre `False` |
| `bot/macro_sensor.py` | Macro Sensor | VIX + SPY SMA-200 via yfinance; kill switch CRO dinamico | Fail-open; thresholds em `config_risco.json`; cache 15 min em `data/macro_cache.json` |
| `scripts/train_bonnie.py` | Pipeline WFO | Walk-Forward Optimization + Optuna; ~14 folds desde 2017 | Cada treino gera vN+1; versoes antigas imutaveis; EMA fixo (50/200) |
| `scripts/promote_model.py` | Model Promoter | Promocao automatica: Sharpe OOS > activo + 0.10; activa shadow mode | NUNCA escreve `config_risco.json`; shadow mode activo bloqueia edicao manual de params |
| `scripts/self_heal.py` | Self-Heal | Gemini sugere parametros dentro de PARAM_BOUNDS; gate semanal | Sugestoes em `data/suggested_config.json` — **nunca aplicar automaticamente** |
| `scripts/daily_briefing.py` | Daily Briefing | Top 5 oportunidades por email (dias uteis 13:30 UTC) | Maximo 5 chamadas Finnhub por briefing |
| `scripts/criteria_review.py` | Criteria Review | Correlacoes de trades reais — bot autodidata (sabados) | NUNCA escreve `config_risco.json`; so analise descritiva |
| `scripts/code_heal.py` | Code Heal | Diagnostico Gemini → GitHub Issue (Ciclo de Castigo) | NUNCA aplica codigo; max 3 tentativas por fingerprint; sanitizacao obrigatoria |

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
| `weekly-audit.yml` | sabados 06:00 UTC | Auditor semanal + criteria review + self-heal |
| `train-bonnie.yml` | domingos 02:00 UTC (timeout 360min) | WFO treino Bonnie + Optuna (~14 folds) |
| `daily-briefing.yml` | dias uteis 13:30 UTC | Briefing diario por email (top 5 oportunidades) |
| `auto-debug.yml` | trigger em falha de qualquer workflow | Ciclo de castigo: diagnostico Gemini → GitHub Issue |
| `daily-report.yml` | 21:15 UTC seg-sex | Relatorio diario Telegram para o Francisco |
| `security-report.yml` | 21:30 UTC sextas | Relatorio de seguranca semanal |
| `apply-suggested-config.yml` | manual (requer input "APLICAR") | Promocao de sugestoes do self-heal; trigger apenas manual |

Secrets: `T212_API_ID`, `T212_API_KEY`, `FINNHUB_TOKEN`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `GEMINI_API_KEY`, `SMTP_USER`, `SMTP_PASS`, `BRIEFING_EMAIL`.

---

## 5. Estado Actual

<!-- ESTADO-ACTUAL-START -->
**Actualizado em:** 2026-05-31 18:10 UTC

- **Bot status:** `active` | Ultimo ciclo: `2026-05-31T18:10Z`
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
| 2026-05-31 | `fed02bf` | bot: emergency cycle 2026-05-31 17:10 UTC [skip ci] |
| 2026-05-31 | `c153e6e` | feat: sample_weight punishment logic for SL hits |
| 2026-05-31 | `3ddc597` | feat: evaluate_challenger.py — The Judge (champion vs challenger) |
| 2026-05-31 | `3421001` | feat: mixed dataset training — historical + real + shadow trades |
| 2026-05-31 | `0046af3` | feat: Bonnie observation mode — ML inference logged without veto power |
| 2026-05-31 | `86c2136` | fix: align ATR multipliers in retrain_bonnie.py with production config |
| 2026-05-31 | `dec6202` | chore: update news.json [skip ci] |
| 2026-05-31 | `dfd2ee2` | feat: simulate_shadow_exits — compute outcomes for shadow trades |
| 2026-05-31 | `682442e` | feat: shadow ledger — capture 8 features for all vetoed signals |
| 2026-05-31 | `c3f452c` | docs: add critical architectural rules to MEMORY_ERRORS.md and CLAUDE.md |
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

## 9. Regras Absolutas

Estas regras nao podem ser violadas por nenhum agente, script ou PR — nem sequer com boas intencoes:

1. **LLM sugere, humano aprova, sistema aplica.** O Gemini/qualquer LLM apenas propoe parametros dentro de limites pre-definidos (`PARAM_BOUNDS`). Nenhuma sugestao e aplicada automaticamente.
2. **`config_risco.json/_absolute_limits` e sagrado.** Nenhum codigo (self-heal, auditor, ciclo de castigo) escreve neste bloco. So o Francisco, manualmente.
3. **Ciclo de castigo: zero auto-merge.** `auto-debug.yml` cria issues e comenta — nunca abre PRs, nunca aplica patches. Cada fix requer aprovacao manual.
4. **Relatorios para o Francisco: zero jargao tecnico.** Sharpe → "qualidade dos lucros"; drawdown → "pior queda"; win rate → "taxa de acerto". Ver tabela em "Relatorios para o Francisco".
5. **O bot opera dentro das regras do mercado.** Nenhuma logica pode explorar falhas de mercado, ordens manipulativas, ou qualquer pratica contraria a teoria economica consolidada.
6. **Shadow mode activo = sem promocao manual.** Se `data/beta/shadow_mode.json` tem `"active": true`, nao editar `optimized_backtest_params.json` manualmente — o pipeline gere automaticamente.

---

## REGRAS DE ARQUITECTURA

### NO GHOST VETOES (Regra de Ouro do Motor Autodidata)
Se um sinal for gerado pelo Clyde e depois vetado por qualquer razão
(Bonnie estática, CRO, falta de liquidez, Social Veto), o sistema é
OBRIGADO a registar as 8 features técnicas desse sinal no log com
"execution_type": "shadow_rejected".
A perda do contexto de um sinal falhado é tratada como fatal exception
na arquitectura. Nunca criar regras de exclusão que não preservem o
estado técnico completo do momento da rejeição.

As 8 FEATURE_COLS obrigatórias a preservar:
["rsi_14", "volume_ratio", "atr_pct", "price_vs_ema20",
 "price_vs_ema50", "price_vs_ema200", "momentum_1m", "momentum_3m"]

### ALINHAMENTO ATR OBRIGATÓRIO
Qualquer script de treino (retrain_bonnie.py, train_bonnie.py, learner.py)
DEVE ler os multiplicadores ATR de config_risco.json:
  TP: config["atr_tp_mult"] (actualmente 4.25)
  SL momentum: config["atr_stop_mult_momentum"] (actualmente 2.0)
  SL value: config["atr_stop_mult_value"] (actualmente 1.75)
Nunca hardcodar estes valores. Verificar alinhamento antes de qualquer
refactoring que toque em ficheiros de treino.

### CAMPEÃO vs DESAFIANTE
O modelo activo em produção é o "Campeão". Qualquer novo modelo treinado
é o "Desafiante" e corre em Shadow Mode antes de ser promovido.
Promoção apenas via scripts/evaluate_challenger.py (O Juiz).
Nunca substituir ficheiros de produção manualmente.

### DATASET DE TREINO MISTO
O dataset de treino da Bonnie tem 3 fontes, todas obrigatórias:
1. Dados históricos (corpus yfinance)
2. Trades reais (beta_trades.json)
3. Shadow trades (execution_type: "shadow_rejected" + resultado simulado)
Treinar apenas com dados históricos é considerado regressão arquitectural.

---

## 10. Ficheiros de Estado

Ficheiros que persistem estado entre ciclos — **nao apagar manualmente**:

| Ficheiro | Quem escreve | Para que serve |
|---|---|---|
| `data/daily_flags.json` | `bot/cro.py`, `bot/notifier.py` | Guards anti-spam Telegram (1 alerta/dia por flag) |
| `data/circuit_breaker_state.json` | `bot/watchdog.py` | Estado dos circuit breakers (contadores de falha) |
| `data/macro_cache.json` | `bot/macro_sensor.py` | Cache VIX + SPY SMA-200 (TTL 15 min) |
| `data/beta/shadow_mode.json` | `scripts/promote_model.py` | Estado do shadow mode: modelo em teste + data de inicio |
| `data/suggested_config.json` | `scripts/self_heal.py` | Sugestao pending do Gemini (aguarda aprovacao manual) |
| `data/audit_weekly.json` | `bot/auditor.py` | Ultimo relatorio do auditor semanal |
| `data/criteria_insights.json` | `scripts/criteria_review.py` | Correlacoes do bot autodidata (trades reais) |
| `data/blocked_tickers.json` | Manual (Francisco) | Tickers bloqueados manualmente (formato T212, ex: `HPE_US_EQ`) |
| `data/beta/code_heal_state.json` | `scripts/code_heal.py` | Fingerprints de erros + contagem de tentativas (max 3) |
| `data/beta/self_heal_state.json` | `scripts/self_heal.py` | Gate semanal (minimo 6 dias entre execucoes) |
| `data/throttler_state.json` | `bot/throttler.py` | Cursor do WatchlistThrottler (persiste entre ciclos) |
| `models/registry.json` | `scripts/promote_model.py` | Versao activa + historico de todas as versoes Bonnie |

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

## Daily Briefing

- Script: `scripts/daily_briefing.py`
- Workflow: `.github/workflows/daily-briefing.yml` (dias úteis 13:30 UTC)
- Bloqueio manual: `data/blocked_tickers.json` (ticker em formato T212, ex: `HPE_US_EQ`)
- Gate no bot: `_apply_manual_block()` em `bot/phase0.py` a seguir ao social veto
- REGRA: rate limit Finnhub news — máximo 5 chamadas por briefing (1 por ticker top 5)
- Secrets necessários: `SMTP_USER`, `SMTP_PASS` (Gmail App Password), `BRIEFING_EMAIL`
- Fail-open: erros de email/API nunca abortam — `_apply_manual_block` devolve todas as oportunidades se ficheiro ausente/corrompido

## Agente Auditor

- Script: `bot/auditor.py`
- Workflow: `.github/workflows/weekly-audit.yml` (sabados 06:00 UTC)
- Output: `data/audit_weekly.json` (escrita atomica; commitado com `[skip ci]`)
- REGRA: o auditor NUNCA escreve em `config_risco.json` directamente
- Padroes detectados: sinais fortes perdedores, Bonnie-aprovados negativos, CRO vs outcome, regime vs SPY, hora do dia
- `param_suggestions[].auto_apply` e sempre `False` — qualquer ajuste requer decisao manual

---

## Self-Healing

- Script: `scripts/self_heal.py` (corre apos o auditor semanal, sabados ~06:05 UTC)
- Sugestoes: `data/suggested_config.json` — **nunca aplicar automaticamente**
- Estado: `data/beta/self_heal_state.json` — gate semanal (minimo 6 dias entre execucoes)
- Limites absolutos: `config_risco.json/_absolute_limits` — **NUNCA alterar**
- Workflow de promocao: `.github/workflows/apply-suggested-config.yml` — trigger apenas manual; requer input "APLICAR"
- REGRA: Gemini sugere dentro de limites. Humano aprova. Sistema aplica.
- Parametros que o Gemini pode afinar (jaula hardcoded em `PARAM_BOUNDS`):
  - `tamanho_maximo_posicao` [0.40, 1.00]
  - `vix_caution_threshold` [15, 25]
  - `vix_kill_switch_threshold` [30, 40]
  - `vix_total_kill_threshold` [42, 50]
  - `cash_is_king_multiplier` [0.10, 0.50]
  - `mean_reversion_rsi_max` [25, 40]
  - `mean_reversion_max_vix` [15, 25]
- O Gemini NUNCA toca em: `permite_comprar`, `motivo_bloqueio`, `estado_emocional`, `_absolute_limits`, nem em `optimized_backtest_params.json`
- Validacao em 3 camadas: allowlist → range/magnitude → sanity (baseline anti-alucinacao + ordenacao VIX)

---

## CRO Dinamico

- **Macro sensor:** `bot/macro_sensor.py` (VIX + SPY SMA-200 via yfinance, cache 15 min em `data/macro_cache.json`)
- **Kill Switch:** VIX ≥ 35 → veta MOMENTUM (Cash is King); VIX ≥ 45 → veta tudo (Kill Switch Total)
- **SPY abaixo SMA-200:** regime forçado para `bear_correction` independente do classificador
- **Thresholds:** nunca hardcodar — todos em `config_risco.json` (`vix_kill_switch_threshold`, `vix_total_kill_threshold`, `vix_caution_threshold`, `cash_is_king_multiplier`)
- **Alerta Telegram:** `_send_kill_switch_alert()` em `bot/cro.py` — 1×/dia via `daily_flags.json` (flag `macro_kill_{mode}`)
- **Fail-open:** offline sem cache → `kill_switch=False`, bot continua normalmente
- **Log obrigatório:** `[CRO] VIX={x} kill_switch={y} macro_mode={z} regime={r} → risk_factor={f}`
- **MEAN_REVERSION:** estratégia VALUE com RSI < 35 — permitida em `bear_correction` a 0.25×; bloqueada se VIX ≥ 35
- **`data/macro_cache.json`** deve estar no `git add` do workflow `run-trading-bot.yml`

---

## Pipeline de Treino Offline

- **Script:** `scripts/train_bonnie.py` (WFO 36m treino / 6m teste / passo 6m, ~14 folds desde 2017)
- **Workflow:** `.github/workflows/train-bonnie.yml` (domingos 02:00 UTC, timeout 360min)
- **Modelos:** `models/bonnie_params_vN.json` — imutáveis, nunca editar versões antigas
- **Relatórios:** `models/bonnie_train_report_vN.md` — tabela por fold, métricas OOS
- **Índice:** `models/registry.json` — versão activa + histórico de todas as versões
- **Promoção:** `scripts/promote_model.py` — critério: Sharpe OOS > activo + 0.10 E gates passados
- **Shadow Mode:** `data/beta/shadow_mode.json` — `{"active": true, "model": "vN", "start": "..."}` activo após promoção
- **Alvo de promoção:** `data/beta/optimized_backtest_params.json` (nunca `config_risco.json`)

### Dataset e Promoção ML (Fase 3)
- **Dataset:** MISTO (histórico + reais + shadow) — ver `load_mixed_dataset()` em `train_bonnie.py`
- **Pesos de penalização:** `config_risco.json/training_weights` (real_sl_hit=3.0, shadow_sl_hit=1.5…)
- **Promoção ML:** `scripts/evaluate_challenger.py` (O Juiz) — corre após o treino no workflow
- **OOS set:** shadow trades dos últimos 30 dias (isolados do treino — garantia de pureza)
- **Campeão activo:** `models/bonnie_champion.pkl` + `models/bonnie_champion_meta.json`
- **Critérios:** `config_risco.json/challenger_promotion_criteria` (gates + ≥2 métricas sem piorar)

### Regras do pipeline
- `models/bonnie_params_vN.json` são **imutáveis** após escrita — qualquer re-treino gera vN+1
- `promote_model.py` **nunca** escreve em `config_risco.json`
- Quando `shadow_mode.json` está activo, não editar `optimized_backtest_params.json` manualmente — gerido automaticamente
- O Optuna usa EMA fixo (50/200) — apenas RSI, vol, ATR stop/TP, trail e Bonnie threshold são optimizados
- Fase 1 (Optuna) usa Bonnie v4-clean fixo; Fase 2 treina challenger com dataset misto + sample_weight
- Fitness = `median(Sharpe OOS) − 0.5 × std(Sharpe OOS)` — penaliza instabilidade entre regimes

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

## Ciclos de Aprendizagem

### Bot Autodidata (Romaria de Fim de Semana)

- **Script:** `scripts/criteria_review.py` (sábados, após auditor semanal)
- **Workflow:** `.github/workflows/weekly-audit.yml` — step "Run criteria review"
- **Output:** `data/criteria_insights.json` (escrita atómica; commitado com `[skip ci]`)
- **Correlações analisadas:** RSI de entrada, volume multiplier, hora UTC, regime
- **REGRA:** nunca escreve em `config_risco.json` — apenas análise descritiva
- **REGRA:** todas as correlações são baseadas em trades **reais** (`beta_trades.json`), sem backtesting

### Claude Autodidata (Ciclo de Castigo)

- **Script:** `scripts/code_heal.py`
- **Workflow:** `.github/workflows/auto-debug.yml`
- **Trigger:** qualquer workflow de produção que falha (`workflow_run: completed, conclusion: failure`)
- **Output:** GitHub Issue com label `auto-debug` e diagnóstico do Gemini
- **Estado:** `data/beta/code_heal_state.json` — fingerprints e contagem de tentativas
- **REGRA:** nunca aplica código automaticamente — apenas cria/comenta issues
- **REGRA:** máximo 3 tentativas por erro (fingerprint estável); ao 3.º, Telegram SOS + status `escalated`
- **REGRA:** nunca se dispara sobre si próprio (guard explícito no workflow)
- **REGRA:** sanitização obrigatória antes de enviar logs ao LLM (remove tokens/segredos)
- **Fingerprint:** `sha256(workflow + step + normalized_error)[:16]` — estável independente de timestamps/paths

---

## Relatórios para o Francisco

**REGRA ABSOLUTA:** relatórios Telegram/email para o Francisco são em linguagem de leigo. Zero jargão técnico. Zero teoria económica.

**Glossário proibido nos relatórios** (usar sempre a descrição em português simples):

| Termo técnico | Substituição obrigatória |
|---|---|
| Sharpe Ratio | Qualidade dos lucros (quanto ganha por cada euro arriscado) |
| Max Drawdown | Pior queda da carteira no período |
| Win Rate | Taxa de acerto (em X negócios, Y correram bem) |
| OOS (Out-of-Sample) | Remover completamente dos relatórios |
| ATR | volatilidade do preço |
| EMA / RSI | indicadores técnicos (não mencionar) |
| VIX | nível de agitação do mercado |
| Bear / Bull | Em queda / Em alta |
| Regime | Estado do mercado |

**Scripts de relatório:**
- `scripts/daily_report_francisco.py` — resumo diário Telegram (21:15 UTC, dias úteis)
- `scripts/security_report.py` — relatório de segurança semanal (sextas 21:30 UTC)
- `bot/notifier.py::enviar_auditoria_semanal()` — auditoria semanal em linguagem simples

**Workflows:**
- `.github/workflows/daily-report.yml` — 21:15 UTC seg-sex
- `.github/workflows/security-report.yml` — 21:30 UTC sextas

---
## Auto-Sync: 2026-05-29 22:12
- PC: DESKTOP-0514V9J
- Ultimo commit: 0feef56 - fix: HTTP error handling for T212 API responses
- Learner: verificar data/beta/ para runs recentes
---