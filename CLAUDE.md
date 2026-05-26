# Diretrizes do FundScope

> **Guia de arquitectura permanente do FundScope.** Este documento define princípios não-negociáveis para qualquer alteração ao bot, executor, notificador ou frontend. As regras abaixo têm precedência sobre qualquer outra secção deste ficheiro.

---

## 1. Princípio Fundamental — Trading 212 API é a Única "Source of Truth"

A **API da Trading 212 é a única fonte autoritativa** sobre o estado do portfolio, posições abertas, cash disponível, preços de execução e P&L realizado. Tudo o resto é cache ou derivado.

### Proibições explícitas
- **PROIBIDO** calcular o valor do portfolio, posições abertas, ou cash disponível **localmente no frontend**.
- **PROIBIDO** recalcular o estado do portfolio em código pós-trade (ex.: `portfolio_value = cash + Σ(qty × last_price_local)`). Esse cálculo diverge sempre da T212 (slippage, fees, FX, settlements pendentes).
- **PROIBIDO** o frontend (GitHub Pages) ler ficheiros locais como `position_ledger.json` como verdade absoluta. O ledger é apenas espelho da API.

### Fluxo obrigatório
1. Após **qualquer** trade executado (`execute_trade` ou `execute_exit` em `bot/execution.py`), chamar imediatamente `get_full_portfolio_state()` em `bot/data_layer.py` para resincronizar com a T212 API.
2. O estado sincronizado é escrito em `data/beta/portfolio.json` (e equivalentes consumidos pelo site).
3. O frontend lê apenas os JSONs gerados pelos agentes Python — **nunca calcula nada**. Aplica-se a Regra de Ouro do `ROADMAP_FRONTEND.md`: *"agentes Python cospem JSONs, frontend só lê"*.

### Em caso de discrepância
Se o ledger local divergir da T212 API, **a API ganha sempre**. Logar a discrepância em `data/beta/sync_warnings.json` e sobrescrever o ledger. Nunca o contrário.

---

## 2. Regra do `bot_status` — Heartbeat LED

O bot deve escrever **a cada ciclo** o ficheiro `data/beta/status.json` com o seguinte formato mínimo:

```json
{
  "last_check": "2026-05-26T14:30:00Z",
  "bot_status": "active"
}
```

### Onde escrever
- Em `bot/phase0.py`, no **fim de cada ciclo** (sucesso ou falha controlada), antes do `return`.
- Em modo de erro fatal (excepção não tratada que aborte o ciclo), escrever `"bot_status": "error"` num `try/finally` no topo do ciclo.

### Para que serve
Este ficheiro alimenta o **"heartbeat LED" verde/vermelho** no site (GitHub Pages):
- Verde → `now() - last_check < 15min` e `bot_status == "active"`.
- Vermelho → caso contrário (bot caiu, GitHub Actions não correu, mercado fechado por demasiado tempo).

### Formato do timestamp
ISO 8601 com sufixo `Z` (UTC). Usar `datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")`. Nunca usar timezone local.

---

## 3. Regra de Isolamento — Falhas Não-Cascateantes

**Telegram, logs locais, escrita de JSONs e API T212 são canais independentes.** A falha de um **nunca** pode abortar os outros.

### Padrão obrigatório
Todo o I/O lateral (Telegram, ficheiros de relatório, webhooks) deve estar embrulhado em `try/except` isolado, com log do erro e continuação:

```python
# Após executar um trade
try:
    enviar_trade_executada(result, modo="LIVE")
except Exception as e:
    logger.error(f"[notifier] falha enviar_trade_executada: {e}", exc_info=True)
    # NÃO re-raise. O trade já foi executado. Continuar.

try:
    append_to_beta_trades(result)
except Exception as e:
    logger.error(f"[ledger] falha append_to_beta_trades: {e}", exc_info=True)
    # Continuar mesmo assim. Resync com T212 corrigirá.
```

### Hierarquia de prioridade (mais crítico → menos crítico)
1. **Execução T212** (ordem enviada) — falhar aqui aborta o trade.
2. **Resync com API T212** (estado autoritativo) — falhar aqui marca o ciclo como degradado mas não aborta.
3. **Persistência local** (ledger, beta_trades.json) — falhar aqui é loggado, não aborta.
4. **Notificações Telegram** — falhar aqui é loggado, **nunca** aborta.

### Anti-padrão
**Nunca** usar `raise` dentro de blocos de notificação ou logging. **Nunca** depender de Telegram para o bot continuar.

---

## 4. Regra de Notificações — Imediatas, Não Acumuladas

Cada **BUY** ou **SELL** executado com sucesso (confirmado pela T212) deve disparar **imediatamente** a notificação correspondente.

### Função canónica
`bot/notifier.py::enviar_trade_executada(result, modo)`

- `result`: dict retornado por `execute_trade()` ou `execute_exit()` com `ticker`, `side`, `qty`, `price`, `value`, `pnl` (se SELL), `order_id`, `timestamp`.
- `modo`: string `"LIVE"` ou `"DRY-RUN"`.

### Onde chamar
- Em `bot/execution.py`, **dentro** de `execute_trade()` e `execute_exit()`, **após confirmação da T212** e **antes do `return`**.
- **NUNCA** acumular trades para enviar no fim do ciclo (`phase0.py`). Isso introduz latência e perde notificações em caso de crash a meio do ciclo.

### Outras notificações já existentes (manter)
- `enviar_oportunidade(...)` — quando Bonnie aprova uma análise mas o trade ainda não foi executado.
- `enviar_despertar()` — abertura de mercado.
- `enviar_boa_noite()` — fecho de mercado / fim do dia.

### Checklist ao adicionar nova lógica de trade
- [ ] Chamei `enviar_trade_executada(result, modo)` imediatamente após confirmação?
- [ ] A chamada está dentro de `try/except` (Regra 3)?
- [ ] O `result` contém todos os campos esperados pelo notificador?

---

## 5. Regra de Timestamps — Rastreabilidade em GitHub Actions

No **início** e no **fim** de cada verificação de mercado em `bot/phase0.py`, escrever um timestamp formatado em `stdout` para que o log do GitHub Actions seja navegável.

### Formato
```python
from datetime import datetime, timezone

def _ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

# Início do ciclo
print(f"[{_ts()}] === FundScope phase0 START ===", flush=True)

# ... lógica do ciclo ...

# Fim do ciclo
print(f"[{_ts()}] === FundScope phase0 END (status={status}, trades={n_trades}) ===", flush=True)
```

### Pontos de log obrigatórios em `phase0.py`
1. Entrada no ciclo (`START`).
2. Após `get_full_portfolio_state()` (estado sincronizado).
3. Antes de cada `execute_trade` / `execute_exit`.
4. Após cada execução (com `order_id` retornado).
5. Saída do ciclo (`END` com resumo).

### Por que importa
- O GitHub Actions log é a única caixa-preta quando algo falha em produção.
- Sem timestamps, é impossível correlacionar com a T212 API (que devolve `executed_at`) ou com mensagens Telegram (que têm timestamp próprio).
- `flush=True` é obrigatório — sem ele, o buffer só descarrega no fim do processo e perde-se ordem cronológica.

---

## Checklist de Code Review (qualquer PR ao bot)

Antes de fazer merge de qualquer alteração ao bot, confirmar:

- [ ] Nenhum cálculo de portfolio/posição é feito localmente após um trade — só resync via T212 API.
- [ ] `data/beta/status.json` é actualizado no fim do ciclo.
- [ ] Toda a chamada a `notifier`, escrita de ficheiro ou webhook está em `try/except` isolado.
- [ ] Cada `execute_trade`/`execute_exit` chama `enviar_trade_executada` imediatamente após confirmação.
- [ ] `phase0.py` tem `print(..., flush=True)` com timestamp no início e fim do ciclo.
- [ ] Frontend (HTML/JS) não foi modificado para calcular estado — apenas lê JSONs.

---

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
## Auto-Sync: 2026-05-26 23:02
- PC: DESKTOP-0514V9J
- Ultimo commit: 660c517 - feat(notifier): reescrever enviar_trade_executada com novo formato
- Learner: verificar data/beta/ para runs recentes
---
