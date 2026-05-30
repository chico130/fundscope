# REGRA DE OURO DO PROJETO: ZERO ERROS REPETIDOS

Sempre que o Claude alterar código neste repositório, deve ler este ficheiro PRIMEIRO
e garantir que não reintroduz nenhum dos erros abaixo.

---

## ERROS CONHECIDOS E RESOLVIDOS

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

## INVESTIGAÇÃO ACTIVA

_(nenhuma em curso)_

---

## REGRAS DE EDIÇÃO OBRIGATÓRIAS

1. Antes de editar sw.js: confirmar que a estratégia de cache para ficheiros .json é network-first
2. Antes de editar qualquer HTML: confirmar que os fetch() de dados .json têm `?v=${Date.now()}`
3. Antes de editar bot/phase0.py ou bot/api_client.py: ler a secção "Ordens BUY rejeitadas" acima
4. Após resolver qualquer item em "INVESTIGAÇÃO ACTIVA": mover para "ERROS CONHECIDOS E RESOLVIDOS" com a solução completa
5. Após introduzir qualquer nova fix: adicionar entrada neste ficheiro com data, sintoma, causa e solução
