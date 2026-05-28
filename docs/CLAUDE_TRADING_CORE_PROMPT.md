---
id: prompt-trading-core
title: "Prompt — Revisão e Correção do Trading Core"
type: prompt
domain: clyde
regime: n/a
tags: [prompt, trading-core, revisao, clyde]
links_obrigatorios:
  parent_moc: "[[MOC_Clyde]]"
  vizinhos: "[[execution]] [[exit_manager]] [[api_client]]"
status: stable
ultima_revisao: 2026-05-23
---

# Prompt para Claude — Revisão e Correcção do Trading Core

> **Navegação:** [↑ Índice](../000-INDEX.md) · [MOC Clyde](../vault/mocs/MOC_Clyde.md)

> Copia este prompt na íntegra para o Claude. Inclui todo o contexto necessário.

---

Estou a desenvolver um bot de trading automático para a Trading 212 (conta demo)
chamado **FundScope Bot**, escrito em Python, a correr via GitHub Actions.

Preciso que faças uma revisão completa do **trading core** — o conjunto de módulos
responsável por executar e fechar ordens na T212. Há um bug crítico confirmado e
possíveis problemas adicionais que precisam de ser identificados e corrigidos.

---

## Contexto do projecto

- **Repositório**: `chico130/fundscope` (GitHub)
- **Stack**: Python 3.11, `requests` para T212 API, `yfinance`/`finnhub` para preços
- **Conta**: T212 Demo (`LIVE_TRADING = False` forçado em config)
- **Execução**: GitHub Actions, corre ~6x/dia durante horário NYSE
- **Ficheiros core do trading**:
  - `bot/api_client.py` — chamadas HTTP à T212 API
  - `bot/execution.py` — lógica de execução de ordens (BUY/SELL)
  - `bot/phase0.py` — orquestrador principal (Fase 1)
  - `bot/position_ledger.py` — ledger local das posições
  - `data/beta/positions_ledger.json` — estado actual das posições
  - `docs/T212_API_MANUAL.md` — manual da API T212 (já criado, lê antes de tudo)

---

## Bug crítico confirmado — SELL falha em posições fraccionárias

**Comportamento actual** (`bot/execution.py`, função `execute_trade`, linha ~195):
```python
if proposed.side.upper() == "SELL":
    ok = api_client.close_position_demo(proposed.ticker)
```

`close_position_demo` chama `DELETE /equity/positions/{ticker}`.

**Problema**: a conta tem posições fraccionárias:
- `ARM_US_EQ`: qty = **0.4886** (fraccionária)
- `GOOGL_US_EQ`: qty = **0.28979725** (fraccionária)

A T212 devolve **HTTP 404** quando se faz DELETE numa posição fraccionária,
mesmo que a posição exista. O endpoint DELETE só funciona para quantidades inteiras.

**Solução correcta** (documentada em `docs/T212_API_MANUAL.md`, secção 5):
```python
def close_position_demo(ticker: str, quantity: float) -> bool:
    is_fractional = (quantity % 1 != 0)
    if is_fractional:
        # Usar SELL MARKET com quantidade exacta
        resp = POST /equity/orders/market {
            "ticker": ticker,
            "quantity": quantity,  # positivo — T212 interpreta como SELL
            "timeValidity": "DAY"
        }
        return resp is not None
    else:
        return DELETE /equity/positions/{ticker}
```

**O que precisas de fazer**:
1. Corrigir `close_position_demo` em `bot/api_client.py` para aceitar `quantity`
   e usar a lógica acima
2. Actualizar todos os call sites de `close_position_demo` para passar `quantity`:
   - `bot/execution.py` (função `execute_trade`)
   - Qualquer outro lugar que chame `close_position_demo`
3. Garantir que `execute_exit` em `bot/execution.py` passa `position["quantity"]`
   correctamente

---

## Revisão completa que precisas de fazer

Além do bug crítico acima, faz uma revisão end-to-end de todo o trading core:

### A. Verificar toda a lógica de execução de ordens
- `execute_trade` lida correctamente com BUY e SELL?
- O gate de mercado fechado está no sítio certo (está em `phase0.py` mas
  não em `execution.py` — é isso intencional)?
- O `execute_exit` passa todos os campos necessários para `execute_trade`?

### B. Sincronização do ledger com a T212
- `position_ledger.py` — como é feito o sync com `GET /equity/portfolio`?
- O ledger é actualizado após cada BUY/SELL executado com sucesso?
- O que acontece se o Actions runner for interrompido a meio de uma ordem?

### C. Prevenção de ordens duplicadas
- Existe protecção contra submeter a mesma ordem duas vezes?
- O `beta_trades.json` é verificado antes de cada BUY?

### D. Error handling
- O `_post` não tem retry (deliberado para evitar ordens duplicadas) —
  mas o `_delete` tem retry. Após a correcção do SELL para usar `_post`,
  o retry no SELL é seguro? (SELL de posição já fechada devolve 404,
  não executa duas vezes — portanto retry em SELL é provavelmente seguro.)
- Os logs incluem agora `status_code` (corrigido recentemente) — verificar
  que todos os paths de erro relevantes incluem contexto suficiente.

### E. Métricas da conta
Após corrigir o trading core, implementa a recolha e exposição das seguintes
métricas da conta T212, a actualizar automaticamente a cada ciclo:

```
- equity_total_eur          (cash.total + posições convertidas)
- cash_free_eur             (cash.free)
- pnl_unrealised_eur        (soma ppl de todas as posições, convertida)
- pnl_realised_eur          (dos trades fechados em beta_trades.json)
- win_rate_pct              (trades com resultado > 0 / total fechados)
- max_drawdown_pct          (peak equity vs current equity)
- n_positions_open          (len de positions_ledger)
- largest_position_pct      (maior exposição individual em % de equity)
- days_since_last_trade     (desde último trade em beta_trades.json)
- sharpe_ratio_approx       (se >= 5 trades disponíveis, senão null)
```

Guarda em `data/beta/account_metrics.json` a cada ciclo.
Expoem no site via o workflow existente (padrão dos outros JSONs em `data/beta/`).

---

## Ficheiros que deves ler antes de qualquer alteração

1. `docs/T212_API_MANUAL.md` — manual da T212 API com comportamento real documentado
2. `bot/api_client.py` — cliente HTTP actual
3. `bot/execution.py` — lógica de execução actual
4. `bot/phase0.py` — orquestrador (funções `_execute_phase1` e `execute_exit`)
5. `bot/position_ledger.py` — ledger de posições
6. `data/beta/positions_ledger.json` — estado actual (ARM 0.4886, GOOGL 0.2898)
7. `config_risco.json` — configuração de risco
8. `data/beta/beta_trades.json` — histórico de trades

---

## Prioridades de implementação

1. **[CRÍTICO]** Corrigir SELL de posições fraccionárias (`close_position_demo` + call sites)
2. **[ALTO]** Revisão completa dos pontos A–D acima
3. **[MÉDIO]** Implementar recolha de métricas da conta (`account_metrics.json`)
4. **[BAIXO]** Documentar qualquer comportamento inesperado encontrado no `T212_API_MANUAL.md`

---

## Restrições importantes

- `LIVE_TRADING` deve permanecer `False` — nunca tocar nesta flag
- Não alterar a estrutura de `beta_trades.json` (outros módulos dependem dela)
- Não alterar a estrutura de `positions_ledger.json` (site consome este ficheiro)
- Qualquer novo ficheiro de dados deve ir para `data/beta/`
- Manter o padrão de logging: `log_decision()` para eventos normais,
  `log_error()` para falhas
- Não fazer retry em `_post` (risco de ordens duplicadas em BUY) —
  mas para SELL via `_post` o retry é aceitável (ver secção D acima)
