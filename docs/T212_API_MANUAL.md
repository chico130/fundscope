---
id: t212-api-manual
title: "Trading 212 API — Manual de Comportamento Real (Demo)"
type: spec
domain: infra
regime: n/a
tags: [spec, trading212, api, manual, infra]
links_obrigatorios:
  parent_moc: "[[MOC_Infraestrutura]]"
  vizinhos: "[[atom-trading212]] [[MOC_Clyde]]"
status: stable
ultima_revisao: 2026-05-23
---

# Trading 212 API — Manual de Comportamento Real (Demo)

> **Navegação:** [↑ Índice](../000-INDEX.md) · atom [Trading212](../vault/atoms/atom-trading212.md)

> Documento baseado em testes empíricos contra `demo.trading212.com/api/v0`.
> **Última verificação: 2026-05-23** (via `scripts/t212_contract_test.py`).
>
> ⚠️ **Antes de mudar `bot/api_client.py`**: corre o contract test e confirma
> que as premissas deste manual ainda batem com a realidade. Se algum teste
> falhar, este documento e o código têm de ser actualizados em conjunto.

```bash
PYTHONPATH=. python scripts/t212_contract_test.py
```

---

## 1. Autenticação

```
Header:    Authorization: Basic <base64(api_id:api_secret)>
.env vars (demo, runtime do bot):  T212_API_ID  (~37 chars)
                                   T212_API_KEY (~43 chars)
.env vars (live, contract test):   T212_LIVE_API_ID
                                   T212_LIVE_API_KEY
Base URL demo:  https://demo.trading212.com/api/v0
Base URL live:  https://live.trading212.com/api/v0
```

- **Schema confirmado**: HTTP Basic. A combinação `id:secret` é codificada em
  base64 — qualquer outra forma (raw key, Bearer) devolve **401**.
- A chave demo é gerada em `T212 demo app → Settings → API`. A chave live é
  gerada na app live e **é diferente** — não a uses na demo nem vice-versa.
- Chaves podem ser revogadas pelo owner. Se rodares a chave em produção sem
  actualizar `.env` local, recebes **401** em todos os endpoints até refrescar.
- Em runtime do bot, [[bot/config.py]] carrega só `T212_API_ID`/`T212_API_KEY`
  e aponta para `T212_BASE_URL_DEMO`. As env vars `T212_LIVE_*` existem apenas
  para o `scripts/t212_contract_test.py --env live`.

---

## 2. Endpoints GET (leitura)

### GET /equity/portfolio → array de posições

```json
[
  {
    "ticker": "ARM_US_EQ",
    "quantity": 0.4886,
    "averagePrice": 289.74621367,
    "currentPrice": 304.08,
    "ppl": 6.03,
    "fxPpl": 0,
    "initialFillDate": "2026-05-22T16:30:00.000+03:00",
    "frontend": "API",
    "maxBuy": 89561.5114,
    "maxSell": null,
    "pieQuantity": 0
  }
]
```

- `quantity` pode ser fraccionário
- `ppl` em USD (moeda nativa do instrumento); `fxPpl` é a componente cambial
- `maxSell: null` ocorre frequentemente em demo — **não significa "não podes
  vender"**. O SELL via POST market funciona na mesma.
- Array vazio = sem posições (ou conta demo resetada)

### GET /equity/account/cash → resumo de cash

```json
{ "free": 4877.88, "total": 5027.65, "ppl": 6.03,
  "result": 0, "invested": 142.63, "pieCash": 0, "blocked": 0 }
```

- `free` em EUR — disponível para novas ordens
- Bug conhecido demo: pode devolver `{"free": 0, "total": 0}` transitoriamente
  com posições abertas. Verificar `n_positions > 0` antes de abortar o ciclo.

### GET /equity/orders → array de ordens pendentes

```json
[
  {
    "id": 49400104422,
    "type": "MARKET",
    "ticker": "ARM_US_EQ",
    "quantity": -0.4886,           // negativo = SELL
    "side": "SELL",
    "status": "NEW",
    "filledQuantity": 0,
    "extendedHours": false,
    "createdAt": "2026-05-23T17:03:31.649+03:00",
    "instrument": { "ticker": "ARM_US_EQ", "name": "ARM", "isin": "...", "currency": "USD" }
  }
]
```

- O sinal de `quantity` codifica o lado (positivo=BUY, negativo=SELL).
  O campo `side` é apenas informativo na response.

---

## 3. POST /equity/orders/market — **schema mínimo obrigatório**

```json
{ "ticker": "ARM_US_EQ", "quantity": -0.4886 }
```

**Convenção do sinal:**
- `quantity > 0` → ordem **BUY**
- `quantity < 0` → ordem **SELL** (fecha posição parcial ou total)

**Campos aceites:**

| Campo | Tipo | Obrigatório | Notas |
|---|---|---|---|
| `ticker` | string | sim | formato `SYMBOL_MARKET_TYPE` (ver §5) |
| `quantity` | number | sim | fraccionário OK; sinal codifica lado |
| `extendedHours` | boolean | não | default false |

**⚠️ Campos REJEITADOS (causam HTTP 400 "Invalid payload"):**

- `timeValidity` — **não é aceite no MARKET** (é só do LIMIT, ver §4)
- `side` — T212 ignora; usa o sinal da `quantity` em vez disto
- `timeInForce` — só na response, não no request

### Erros comuns

| HTTP | `type` | Significado |
|---|---|---|
| 200 | — | Ordem aceite; ver `id` e `status` na response |
| 400 | `invalid-request` | Payload tem campo a mais (`timeValidity`, etc.) |
| 400 | `min-quantity-exceeded` | qty < `must trade at least X.XXXX` (varia por ticker) |
| 400 | `selling-equity-not-owned` | quantity negativa maior que `quantity` em portfolio |
| 401 | — | Auth inválida |
| 429 | — | Rate limit (esperar ≥ 30s) |

### Mínimos por ticker (descobertos empiricamente)

| Ticker | Min qty |
|---|---|
| ARM_US_EQ | 0.00379136 |
| F_US_EQ | 1.16273410 |
| outros | erro `min-quantity-exceeded` devolve o valor exacto no `detail` |

---

## 4. POST /equity/orders/limit — schema **DIFERENTE** do MARKET

```json
{
  "ticker": "F_US_EQ",
  "quantity": 1,
  "limitPrice": 14.50,
  "timeValidity": "DAY"
}
```

| Campo | Tipo | Obrigatório | Notas |
|---|---|---|---|
| `ticker` | string | sim | — |
| `quantity` | integer | sim | **fraccionários rejeitados** — fallback para MARKET |
| `limitPrice` | number | sim | preço limite (2 decimais) |
| `timeValidity` | string | sim | **"DAY" é o único valor aceite** ("GTC" devolve 400) |

**Diferenças críticas vs MARKET:**

- LIMIT **exige** `timeValidity:"DAY"` (MARKET rejeita o mesmo campo)
- LIMIT **só aceita quantity inteira** (MARKET aceita fraccionário)
- LIMIT **não** suporta convenção de sinal — usa endpoints separados para SELL?
  Ainda não confirmado; o bot usa MARKET para fechar posições.

**Decisão de design no bot:** se `qty` é fraccionário num signal LIMIT,
[`place_order_demo`](../bot/api_client.py) faz fallback automático para MARKET.

---

## 5. Tickers T212 — Formato

Formato: `{SYMBOL}_{MARKET}_{TYPE}`

| Mercado | Sufixo | Exemplo |
|---|---|---|
| NASDAQ/NYSE (USD) | `_US_EQ` | `AAPL_US_EQ`, `ARM_US_EQ` |
| London (GBP) | `_GBP_EQ` | `VOD_GBP_EQ` |
| London (GBX) | `_GBX_EQ` | `BP_GBX_EQ` |
| ETF (USD) | `_US_ETF` | `SPY_US_ETF` |

**Tickers opacos** (quando o prefixo T212 ≠ ticker yfinance):

| T212 | yfinance |
|---|---|
| `MTEd` | `MU` (Micron) |
| `49Vd` | `VST` (Vistra) |
| `0V6d` | `VRT` (Vertiv) |
| `CJ6d` | `CCJ` (Cameco) |
| `ASMLa` | `ASML.AS` (Euronext Amsterdam) |

Mapping mantido em [`bot/api_client.py`](../bot/api_client.py) (`_T212_OPAQUE_TO_YF`)
e [`bot/position_ledger.py`](../bot/position_ledger.py) (`_T212_OPAQUE`).

---

## 6. Fechar posições — **NÃO existe endpoint dedicado**

### ❌ Endpoints que NÃO funcionam (anti-regression — confirmado 2026-05-23)

| Endpoint | Resposta real | Nota |
|---|---|---|
| `DELETE /equity/positions/{ticker}` | **404 page not found** | Endpoint não existe |
| `DELETE /equity/portfolio/{ticker}` | **405 Method Not Allowed** | GET-only |
| `POST /equity/orders/market` + `"side":"SELL"` | **400 Invalid payload** | Campo `side` rejeitado |

> ⚠️ Versões anteriores deste manual afirmaram (incorrectamente) que
> `DELETE /equity/positions/{ticker}` era o endpoint de fecho. **Era falso.**
> O bot adoptou esse pattern e durante meses devolveu `True` (DELETE → 404 →
> mas o flow de cima interpretava o caminho errado) enquanto a posição
> nunca fechava. Hoje (2026-05-23) o flow correcto está em
> [`close_position_demo`](../bot/api_client.py).

### ✅ Forma correcta — POST market com quantity negativa

```python
# Em bot/api_client.py
def close_position_demo(ticker, quantity):
    cancel_pending_orders_demo(ticker)              # limpa BUYs órfãs primeiro
    return _post("/equity/orders/market", {
        "ticker":   ticker,
        "quantity": -abs(quantity),                  # NEGATIVO = SELL
    }) is not None
```

A ordem fica `status: NEW` até ao próximo open de mercado se for fora de horas.

---

## 7. Cancelar / DELETE de ordens

```
DELETE /equity/orders/{id}
```

- `id` vem do `id` da response do POST que criou a ordem ou do GET /equity/orders
- 200 = cancelada com sucesso
- 404 = ordem já não existe (já preenchida ou já cancelada)

---

## 8. Rate Limits

- **429 Too Many Requests**: aguardar **≥ 30s** antes de novo pedido
- `REQUEST_DELAY_SECONDS` em [`bot/config.py`](../bot/config.py) deve ser ≥ 1.0
- GET endpoints: na prática até ~10 req/min seguros
- POST ordens: 1 por segundo no máximo

---

## 9. Instabilidades conhecidas da demo

| Sintoma | Causa provável | Mitigação |
|---|---|---|
| `GET /equity/portfolio` → `[]` | Reset de conta demo ou timeout interno | Retry com backoff |
| `cash.free = 0` com posições abertas | Payload parcial transitório | Não abortar se `n_positions > 0` |
| `maxSell: null` | Behaviour de demo, não bug | Ignorar; tentar SELL na mesma |
| POST market > 10s | Fila de ordens da demo sobrecarregada | Timeout 30s já configurado |
| 503 Service Unavailable | Manutenção planeada / incidente | Não retentar; aguarda ciclo seguinte |

A demo é menos estável que a live. **`positions_ledger.json` é cache LOCAL** —
em conflito, a verdade é sempre `GET /equity/portfolio`.

---

## 10. Métricas da Conta — cálculo

### Equity Total (EUR)
```python
equity_eur = cash["free"]
for pos in positions:
    value_native = pos["currentPrice"] * pos["quantity"]
    if "_US_" in pos["ticker"]:
        value_eur = value_native / eurusd_rate   # USD → EUR
    else:
        value_eur = value_native
    equity_eur += value_eur
```

### P&L Não Realizado (EUR)
```python
# cash["ppl"] já vem em EUR e inclui o fxPpl agregado
pnl_unrealised_eur = cash.get("ppl", 0.0)
```

### P&L Realizado
```python
# A partir dos trades fechados em beta_trades.json
pnl_realised_eur = sum(
    t["result_eur"] for t in trades
    if t.get("closed_at") and t.get("result_eur") is not None
)
```

---

## 11. Demo vs Live — runbook de pre-flight para Fase 3

A spec da Fase 3 (`LIVE_TRADING=True` em [[bot/config.py]]) muda a base URL
para `live.trading212.com/api/v0`. **A paridade de schema com a demo NÃO está
confirmada** — esta secção define o procedimento para validar antes do flip.

### 11.1 Diferenças expectadas (a confirmar pelo contract test)

Estas são hipóteses informadas, não factos. O contract test refuta ou
confirma cada uma. Marca cada item ✅/❌ depois da primeira run live e
actualiza esta secção.

| Aspecto | Demo (validado 2026-05-23) | Live (TBD) | Risco se diferir |
|---|---|---|---|
| Auth scheme (Basic id:secret) | ✅ HTTP Basic | provavelmente igual | baixo |
| Base URL | `demo.trading212.com/api/v0` | `live.trading212.com/api/v0` | sem risco |
| POST market schema | `{ticker, quantity}` (sinal codifica lado) | provavelmente igual | médio — bot quebra silenciosamente |
| POST limit schema | exige `timeValidity:"DAY"` | provavelmente igual | médio |
| Rate limits | ~10 GET/min, 1 POST/s, 429 → 30s wait | possivelmente mais apertados | médio |
| Mínimos por ticker | ARM 0.00379, F 1.16 | possivelmente diferentes | baixo (erro claro) |
| Instabilidades transitórias (cash.free=0, portfolio []) | frequentes | **menos frequentes** (live é mais estável) | positivo |
| `maxSell: null` em algumas posições | frequente | possivelmente sempre populado | baixo |
| Extended hours | `extendedHours:true` aceite | possivelmente igual | baixo |
| Instrumentos disponíveis | superset (inclui CFDs simulados) | subset (só o que está na conta ISA/Invest) | baixo |

### 11.2 Runbook

```powershell
# 1. Gera credenciais live em T212 → Settings → API (conta real, NÃO demo)
#    Anota api_id e api_key — vais precisar dos dois.

# 2. Adiciona ao .env LOCAL (não commitar; nunca expor)
#    T212_LIVE_API_ID=...
#    T212_LIVE_API_KEY=...

# 3. Pre-flight read-only check — verifica auth e schema GET
PYTHONPATH=. python scripts/t212_contract_test.py --env live --i-understand-risk

# 4a. Se 13/13 passes → paridade confirmada. Avança para os restantes
#     pré-requisitos da Fase 3 (ver vault/specs/FASE-1.md §9.4).

# 4b. Se < 13/13 → STOP. Não fazer flip de LIVE_TRADING.
#     Para cada falha:
#       - identificar qual asserto falhou (output do test descreve)
#       - actualizar a tabela §11.1 deste manual marcando ✅/❌
#       - se for diferença de schema, adicionar secção §11.3 abaixo
#       - se for diferença comportamental (rate limit, etc.), §11.4
#       - actualizar bot/api_client.py para suportar ambos os schemas
#         (idealmente via if env == "live" else demo branch)
```

### 11.3 Live differences — diferenças de SCHEMA confirmadas

Inicialmente vazio — popular após primeira run live. Cada entrada deve ter:
endpoint, demo behavior, live behavior, mitigation no `api_client.py`.

*(Sem entradas até à primeira validação contra live.)*

### 11.4 Live differences — diferenças COMPORTAMENTAIS confirmadas

Inicialmente vazio — popular após primeira run live. Cada entrada deve ter:
fenómeno observado em live, frequência, mitigação no bot.

*(Sem entradas até à primeira validação contra live.)*

### 11.5 Custos reais — só aplicáveis em live

A demo é gratuita; live tem custos que demo não modela. Antes da Fase 3,
medir estes em paper trading e ajustar o edge esperado:

- **Spread bid-ask**: T212 mostra preço mid em `currentPrice` mas execuções
  acontecem ao bid/ask. Para tickers líquidos US: spread típico < 0.05%.
  Para small-caps ou ETFs europeus: pode chegar a 0.3%.
- **Conversão cambial EUR↔USD**: T212 cobra 0.15% por trocas automáticas.
  O bot opera maioritariamente em USD com cash EUR → cada BUY/SELL paga este
  fee. Para uma estratégia que visa 1-3% por trade, isto come 5-15% do edge.
- **Slippage de MARKET orders**: em demo a slippage é instantânea ao mid; em
  live pode ser ±0.1% em horas normais, mais em open/close. Considerar
  migrar para LIMIT orders com offset (já está no plano da Fase 3 §7
  prioridade 2 do [[FASE-1.md]]).

### 11.6 Critério de "pronto para flip de LIVE_TRADING"

Todos estes devem estar verdadeiros simultaneamente:

- [ ] `scripts/t212_contract_test.py --env live` devolve 13/13 passes
- [ ] §11.3 e §11.4 deste manual estão actualizadas (mesmo que vazias)
- [ ] [[bot/config.py]] tem `RISK_CONFIG_LIVE_CONSERVATIVE` definido
- [ ] Restantes pré-requisitos da Fase 3 (paper trading, WR, Sharpe) — ver
      [[vault/specs/FASE-1.md|FASE-1]] §9.4
- [ ] Aprovação humana explícita registada em commit dedicado ao flip

---

## 12. Inspecção manual via curl

```bash
# Cuidado: $T212_KEY tem de ser o header Basic completo, não a key crua
export T212_KEY="Basic $(echo -n "$T212_API_ID:$T212_API_KEY" | base64)"

curl -H "Authorization: $T212_KEY" https://demo.trading212.com/api/v0/equity/portfolio
curl -H "Authorization: $T212_KEY" https://demo.trading212.com/api/v0/equity/account/cash
curl -H "Authorization: $T212_KEY" https://demo.trading212.com/api/v0/equity/orders
curl -H "Authorization: $T212_KEY" "https://demo.trading212.com/api/v0/equity/history/orders?limit=50"
curl -H "Authorization: $T212_KEY" https://demo.trading212.com/api/v0/equity/metadata/instruments
```

**Endpoints NÃO disponíveis na API pública T212:**
- Preços em tempo real (usar Finnhub / yfinance)
- Dados OHLCV históricos (usar yfinance)
- Websocket de preços

---

## 13. Histórico de Alterações

| Data | Alteração |
|---|---|
| 2026-05-23 | **Reescrita completa.** Versão anterior continha 3 afirmações falsas que causaram bugs em produção: (i) `timeValidity:"DAY"` documentado como aceite no MARKET (na verdade rejeitado); (ii) `DELETE /equity/positions/{ticker}` documentado como endpoint de fecho (na verdade 404); (iii) POST market descrito como "BUY-only que ignora side" (na verdade aceita quantity negativa para SELL). Toda a informação actual foi validada por `scripts/t212_contract_test.py` (13/13 passes). |
| 2026-05-23 | **§11 Demo vs Live reescrita.** Runbook concreto para pre-flight de Fase 3, tabela de diferenças expectadas a verificar, secções §11.3/§11.4 reservadas para popular após primeira run live, §11.5 sobre custos reais (spread, FX 0.15%, slippage), §11.6 checklist de "pronto para flip". Contract test agora aceita `--env live --i-understand-risk` com guards de segurança. |
