# REGRA DE OURO DO PROJETO: ZERO ERROS REPETIDOS

Sempre que o Claude alterar código neste repositório, deve ler este ficheiro PRIMEIRO
e garantir que não reintroduz nenhum dos erros abaixo.

---

## ERROS CONHECIDOS E RESOLVIDOS

### [2026-05] CRÍTICO — Desalinhamento de parâmetros ATR entre treino e produção
**Sintoma:** O modelo ML treina com alvos irreais e aprende a optimizar um cenário
que nunca acontece em produção.
**Causa raiz:** scripts/retrain_bonnie.py usa TP_ATR_MULT=1.5 e SL_ATR_MULT=1.0.
A produção (config_risco.json / CRO) usa atr_tp_mult=4.25 e atr_stop_mult_value=1.75.
O modelo está a aprender a bater num alvo de lucro que o bot nunca usa na realidade.
**Solução aplicada (2026-05-31):**
- `scripts/retrain_bonnie.py`: removidos `TP_ATR_MULT=1.5` e `SL_ATR_MULT=1.0` hardcoded.
  Substituídos por leitura de `config_risco.json` no topo do módulo:
  `_cfg = json.loads(...)` → `TP_ATR_MULT = _cfg.get("atr_tp_mult", 4.25)`,
  `SL_ATR_MULT_VALUE = _cfg.get("atr_stop_mult_value", 1.75)`,
  `SL_ATR_MULT_MOMENTUM = _cfg.get("atr_stop_mult_momentum", 2.0)`.
  `label_for_observation` usa agora `SL_ATR_MULT_VALUE` (alinhado com a produção VALUE).
- `scripts/train_bonnie.py`: `retrain_bonnie_phase2` actualizado — `rb.SL_ATR_MULT = sl_mult`
  → `rb.SL_ATR_MULT_VALUE = sl_mult` (mantém o alinhamento após a Fase 1 Optuna).
**Prevenção futura:** Qualquer script de treino DEVE ler os multiplicadores ATR
directamente de config_risco.json. Nunca hardcodar TP_ATR_MULT ou SL_ATR_MULT
em scripts de treino. Antes de qualquer refactoring de bonnie.py ou retrain_bonnie.py,
verificar que os multiplicadores estão sincronizados com config_risco.json.

### [2026-05] CRÍTICO — Esquizofrenia da Bonnie (ML em backtest, regras em produção)
**Sintoma:** O modelo ML treinado em backtest.py nunca opera dinheiro real.
Em produção, a Bonnie usa apenas regras estáticas hardcoded (volume_ratio < 1.2, etc.).
**Causa raiz:** Desconexão arquitectural entre bot/bonnie.py (regras) e o .pkl treinado.
**Solução aplicada (2026-05-31):**
- `bot/bonnie.py`: adicionada classe `Bonnie` com singleton `_bonnie`.
  Modo controlado por `config_risco.json → "bonnie_ml_mode"` (`"static"` por defeito).
  Em modo `"observe"`: carrega `models/bonnie_champion.pkl` (se existir) e corre
  `predict_proba` para cada BUY aprovado pelas regras estáticas, logando
  `[BONNIE-ML] ticker=X proba={y:.3f} static_verdict=approved threshold={z}`.
  Se o .pkl não existir, cai silenciosamente para `"static"`.
  Regras estáticas em `filter_proposals` inalteradas — produção não é afectada.
- `bot/phase0.py`: `market_data` em `_apply_bonnie_filter` inclui agora `"features"` por opp,
  propagando o vector de 8 features calculado por `_build_feature_vector` até à Bonnie.
**Prevenção futura:** bot/bonnie.py deve sempre ser capaz de carregar o .pkl activo.
O Modo Observação (log ML sem vetar) é obrigatório antes de activar inferência real.

### [2026-05] CRÍTICO — Amnésia de Dados (features perdidas nos sinais vetados)
**Sintoma:** O sistema rejeita dezenas de sinais mas não guarda as 8 features técnicas
no momento da rejeição. É matematicamente impossível treinar a Bonnie sem estes dados.
**Causa raiz:** logs/trades/YYYY-MM-DD.json grava apenas "Decision Event" genérico
sem o vector de features completo.
**Solução aplicada (2026-05-31):**
- `bot/phase0.py`: `_build_feature_vector(tech, mom_1m, mom_3m)` calcula as 8 features
  a partir de `data["technicals"]` cru (não de `sig.context`, que perde os EMA floats).
  Adicionado `"features"` e `"regime"` a cada oportunidade em `_scan_watchlist_candidates`.
- `bot/logger.py`: `log_shadow_rejected(rejected_by, rejection_reason, features, signal)`
  escreve `data/beta/shadow_ledger.json` (schema `{"shadow_trades": [...], "last_updated": ...}`)
  atomicamente. Chamado em todos os pontos de veto: Bonnie (`_apply_bonnie_filter`),
  social (`_apply_social_veto`), bloqueio manual (`_apply_manual_block`), e todos os
  skips da Fase 1 (`_execute_phase1._skip` via `_SKIP_TO_REJECTED_BY` map).
- `scripts/simulate_shadow_exits.py`: preenche `shadow_result` para registos expirados,
  simulando TP/SL bar a bar com multiplicadores de `optimized_backtest_params.json`.
- `.github/workflows/weekly-audit.yml`: step "Simulate shadow exits" adicionado (sábados).
**Prevenção futura:**
- `_build_feature_vector` DEVE receber `data["technicals"]` (raw de `fetch_single_ticker`),
  NUNCA `sig.context` — este perde os EMA float values usados em `price_vs_emaXX`.
- `simulate_shadow_exits.py` lê multiplicadores ATR de `optimized_backtest_params.json`
  em runtime — nunca hardcodar (ver erro "Desalinhamento de parâmetros ATR" acima).
- `shadow_ledger.json` é ficheiro de estado em `data/beta/` — incluído no `git add data/beta/`
  do `run-trading-bot.yml` (persiste entre ciclos) e no commit do `weekly-audit.yml`.

### [2026-05-29] Cache agressiva no iPhone 13 — stock.html e home não actualizam
**Sintoma:** Página mostra dados desactualizados. Home desactualizada (não mostra ganhos). stock.html consome dados estáticos de data/beta.
**Causa raiz:** Service Worker (sw.js) com estratégia cache-first para todos os `.json` que não estavam na lista `DATA_URLS` explícita (incluindo `data/beta/*.json`, `gains_insights.json`, `ai_insights.json`). Três `fetch()` sem `?t=` caíam em cache-first por acidente mesmo para JSONs dinâmicos.
**Solução aplicada:** (1) `sw.js`: `CACHE_NAME` bumpado para `fundscope-v4`; regra de routing simplificada — qualquer `.json` que não seja `/manifest.json` usa network-first (elimina necessidade de manter lista `DATA_URLS`). (2) `stock.html:553` — `fetchTimeout('news.json')` → `?v=${Date.now()}`. (3) `stock.html:655` — `fetchTimeout('data.json')` → `?v=${Date.now()}`. (4) `watchlist.html:442` — `fetch('data.json')` → `` `data.json?v=${Date.now()}` ``.
**Prevenção futura:** Nunca usar cache-first para `.json` de dados. Regra em sw.js agora é automática: qualquer `.json` ≠ `manifest.json` → network-first. Ao fazer bump de HTML/CSS: incrementar `CACHE_NAME`. Ao adicionar novo `fetch()` de dados: sempre incluir `?v=${Date.now()}` como redundância defensiva.
**Ficheiros afectados:** sw.js, stock.html, watchlist.html

### [2026-05-xx] Watchlist não mostra avaliação do CRO/Clyde
**Sintoma:** A coluna "Avaliação" aparece vazia ou desaparece da watchlist.
**Causa raiz:** stock.html e watchlist.html lêem dados de data/beta via fetch estático. O projecto define que o estado nunca é calculado no browser — os dados têm de vir pré-calculados dos scripts Python e estar actualizados no JSON.
**Solução aplicada:** [a preencher após fix]
**Prevenção futura:** Nunca calcular estado/avaliação no browser. Sempre pré-calcular nos scripts Python e servir via JSON.

### [2026-05-22] Ordens BUY rejeitadas pela T212 (HPE_US_EQ a $42.94 + ARM_US_EQ)
**Sintoma:** Clyde detecta sinal válido, Bonnie aprova, mas a ordem é rejeitada pela API T212. Mensagem Telegram genérica sem código HTTP.
**Causa raiz (duas causas simultâneas):**
1. Payload MARKET incluía `"timeValidity": "DAY"` — campo não aceite pelo endpoint `/equity/orders/market` da T212, que devolve HTTP 400 "Invalid payload". Confirmado via `git show 8a6b27d^:bot/api_client.py` (linhas 325-329 do estado anterior).
2. Para ARM_US_EQ, os logs mostram HTTP 401 Unauthorized — segredo `T212_API_ID` ou `T212_API_KEY` ausente/errado no GitHub Actions nessa data (a autenticação exige `Basic base64(id:secret)`, não chave única).
**Solução aplicada:**
- `8a6b27d` (2026-05-23): `timeValidity` removido de todos os payloads MARKET em `api_client.py`.
- `bcfa30b` (2026-05-22): erros T212 expostos no stdout e no Telegram.
- Série de commits 2026-05-29: `_post()` agora captura `status_code` + body JSON da T212, expõe via `get_last_order_error()`; mensagens Telegram incluem motivo exacto (ex: "HTTP 400 — InsufficientResources: ..."); 429 recebe retry único após 60 s; `InstrumentNotFound` não entra na fila de retry.
- `phase0.py`: validação `insufficient_cash` pré-ordem (cash livre vs tamanho calculado) evita submeter ordens que a T212 vai rejeitar por saldo.
**Prevenção futura:**
- Nunca adicionar `timeValidity` ao endpoint `/equity/orders/market` (só LIMIT o aceita, e apenas `"DAY"`).
- Verificar que `T212_API_ID` **e** `T212_API_KEY` estão ambos definidos nos GitHub Actions secrets.
- Ao adicionar novos campos ao payload T212: testar contra conta demo antes de fazer push.

---

### [2026-05-30] Guards de dedup (daily_flags.json) não persistiam entre ciclos — mensagens Telegram repetidas
**Sintoma:** Mesmas mensagens Telegram (concentração de posição, circuit breaker, relatório CRO, alertas de drawdown/win rate) a chegar múltiplas vezes por dia — até 1×/ciclo de 15 min.
**Causa raiz:** `data/daily_flags.json` (ficheiro de estado de dedup do notifier) nunca estava incluído no `git add` do workflow `run-trading-bot.yml`. Como cada ciclo de 15 min é um runner independente com fresh checkout, o ficheiro era criado em disco, escrito, mas descartado no fim do job. No ciclo seguinte `_read_daily_flags()` devolvia `{}` e todos os guards (`_already_sent_today`, `_already_sent_this_hour`) retornavam sempre `False`. **Bug adicional 1:** `circuit_breaker._trip_alert` chamava `enviar_alerta` directamente, sem guard persistente — ao estado ser por-processo, em cada ciclo o breaker re-abria e re-enviava. **Bug adicional 2:** Alerta de concentração de posição usava guard horário (`_this_hour`) em vez de diário — um estado persistente durante o dia estava a ser enviado até 8×/sessão mesmo com persistência. **Bug adicional 3:** `win_rate_7d` é fracção 0.0–1.0 mas era formatado com `{_wr:.1f}%` sem `×100` em 3 locais → reportava "1.0%" em vez de "100.0%". O threshold de alerta `_wr < 25.0` comparava fracção com percentagem → era sempre `True`, disparando o alerta em todos os ciclos.
**Solução aplicada:**
- `run-trading-bot.yml`: adicionado `data/daily_flags.json` ao `git add` do step "Commit análise e push".
- `data/daily_flags.json`: criado ficheiro inicial `{}` para bootstrapping.
- `bot/circuit_breaker.py`: `_trip_alert` passa agora por `_already_sent_this_hour(f"circuit_{name}")` / `_mark_sent_this_hour(...)` antes de enviar Telegram.
- `bot/phase0.py`: concentração muda de `_already_sent_this_hour` → `_already_sent_today`; win rate formatação `{_wr:.1f}%` → `{_wr*100:.1f}%` em 2 locais; threshold `_wr < 25.0` → `_wr < 0.25`.
- `bot/notifier.py`: `enviar_boa_noite` formata win rate com `{wr*100:.1f}%`.
**Prevenção futura:**
- `win_rate_7d` é **sempre fracção** (0.0–1.0) em todo o codebase — multiplicar por 100 **apenas na formatação**.
- Qualquer novo ficheiro de estado escrito pelo bot em `data/` (fora de `data/beta/`) deve ser adicionado ao `git add` do workflow, caso contrário não persiste entre ciclos.
- Guards de dedup para eventos inter-ciclo devem usar `_already_sent_this_hour` ou `_already_sent_today` — nunca estado in-memory (que reset a cada GitHub Actions run).
**Ficheiros afectados:** run-trading-bot.yml, data/daily_flags.json, bot/circuit_breaker.py, bot/phase0.py, bot/notifier.py

---

### [2026-05-30] CRO vetava novas entradas apenas por cash/sector, não por posição existente acima do limite
**Sintoma:** Alerta "COST_US_EQ representa 12.3% da carteira (limite: 11.0%)" surge no Telegram/log, mas o CRO continua a aprovar novas ordens BUY no mesmo ticker porque a verificação de `max_position_pct` em `_validate_proposal()` comparava apenas o `free_cash` disponível, sem considerar o valor actual da posição existente.
**Causa raiz:** Em `bot/cro.py::_validate_proposal()`, o bloco `if proposed.side == "BUY"` verificava `max_pos_eur > free_cash * 0.95` (cash insuficiente) e concentração sectorial, mas nunca calculava `existing_pct = existing_value / total_equity * 100` nem comparava com `max_pos`. Qualquer nova ordem num ticker já a 12.3% passava o gate.
**Solução aplicada:**
- `bot/cro.py::_validate_proposal()`: antes do check de cash, calcula `existing_value` somando `value`/`value_eur` de todas as posições com o mesmo ticker; se `existing_pct >= max_pos`, devolve `(False, "position_overweight", 0.0, 0.0)`.
- `bot/cro.py`: nova função pública `check_overweight_positions(portfolio_state)` que itera todas as posições, e para cada uma acima do limite envia um alerta Telegram por ticker com dedup diário via `daily_flags.json` (flag `overweight_{ticker}`).
- `bot/phase0.py`: chamada a `check_overweight_positions(state)` em try/except isolado, logo após o bloco `position_concentration` existente.
**Prevenção futura:**
- Em `_validate_proposal()`, a verificação de excesso de posição deve sempre comparar com o valor **actual** da posição existente, não apenas com o tamanho da nova ordem ou com o `free_cash`.
- Qualquer novo gate de risco em `_validate_proposal()` deve ser adicionado **antes** do check de cash (que é o último gate de sizing, não de concentração).
- `check_overweight_positions()` usa flags `overweight_{ticker}` — um flag por ticker por dia. Não usar flag genérica única para múltiplos tickers (perderia especificidade).
**Ficheiros afectados:** bot/cro.py, bot/phase0.py

---

### [2026-05-30] Sessão termina às 20h UTC perdendo 1h de mercado NYSE
**Sintoma:** Bot para às 20h UTC (21h Lisboa) mas o mercado NYSE fecha às 21h UTC.
**Causa raiz:** Schedule do workflow ou lógica `is_market_open` com horário de fecho incorrecto (20h em vez de 21h UTC).
**Solução aplicada:** Último ciclo do workflow movido para 20:45 UTC. `is_market_open` corrigido para 21:00 UTC.
**Prevenção futura:** Horário do mercado NYSE = 14:30–21:00 UTC. Não alterar sem verificar o horário oficial. DST americano afecta UTC offset — testar em ambas as épocas (EST e EDT).
**Ficheiros afectados:** .github/workflows/run-trading-bot.yml, bot/market_hours.py

---

---

### [2026-05-30] Alerta "Ticker inválido" (InstrumentNotFound) disparava a cada ciclo de 15 min
**Sintoma:** Alerta Telegram "[CLYDE] ❌ Ticker inválido: XYZ" repetido a cada ciclo enquanto o mesmo ticker inválido continuar a ser proposto por Clyde (condições técnicas persistentes na watchlist).
**Causa raiz:** `bot/execution.py` — na detecção de `InstrumentNotFound` (T212 não reconhece o instrumento), `enviar_alerta` era chamado directamente sem guard. Clyde pode propor o mesmo ticker em ciclos consecutivos se as condições RSI/EMA se mantiverem. Cada ciclo = novo run GitHub Actions = sem estado in-memory = guard `_already_sent_today` necessário.
**Solução aplicada:**
- `bot/execution.py`: importados `_already_sent_today` e `_mark_sent_today` de `notifier`; alerta `InstrumentNotFound` agora protegido por `_already_sent_today(f"invalid_ticker_{ticker}")` / `_mark_sent_today(...)` — máximo 1 alerta por ticker por dia.
**Prevenção futura:**
- Qualquer alerta ligado a estado persistente (ticker inválido, posição acima do limite, etc.) deve usar guard diário via `daily_flags.json`. Alertas de evento único genuíno (BUY executado, SELL executado) não precisam de guard.
- `invalid_ticker_{ticker}` usa 1 flag por ticker — não flag genérica única.
**Ficheiros afectados:** bot/execution.py

---

## INVESTIGAÇÃO ACTIVA

_(nenhuma em curso)_

---

## REGRAS DO CRO DINÂMICO

**REGRA: O CRO nunca é estático. Verificar sempre VIX antes de ajustar multiplicadores.**
- `bot/macro_sensor.py` é a única fonte de VIX e SPY SMA-200 — nunca duplicar esta lógica noutros módulos.
- Thresholds de kill switch (`vix_kill_switch_threshold`, `vix_total_kill_threshold`) estão em `config_risco.json` — nunca hardcodar em código Python.
- Alertas de kill switch usam `_already_sent_today(f"macro_kill_{mode}")` — 1×/dia por modo, nunca estado in-memory (reseta a cada GitHub Actions run).
- `data/macro_cache.json` deve estar no `git add` do workflow `run-trading-bot.yml` para persistir entre ciclos.
- A cache do macro sensor tem TTL de 15 min — se vazia ou expirada, o sensor faz fetch live; se yfinance falhar, usa cache stale + alerta Telegram.
- Modo fail-open: se macro sensor offline sem cache, `kill_switch=False` — o bot continua sem kill switch ativo (nunca bloquear por ausência de dados).

---

## REGRAS DE EDIÇÃO OBRIGATÓRIAS

1. Antes de editar sw.js: confirmar que a estratégia de cache para ficheiros .json é network-first
2. Antes de editar qualquer HTML: confirmar que os fetch() de dados .json têm `?v=${Date.now()}`
3. Antes de editar bot/phase0.py ou bot/api_client.py: ler a secção "Ordens BUY rejeitadas" acima
4. Após resolver qualquer item em "INVESTIGAÇÃO ACTIVA": mover para "ERROS CONHECIDOS E RESOLVIDOS" com a solução completa
5. Após introduzir qualquer nova fix: adicionar entrada neste ficheiro com data, sintoma, causa e solução
