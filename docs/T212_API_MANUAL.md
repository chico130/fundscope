# Trading 212 API — Manual de Comportamento Real (Demo)

> Documento baseado em testes reais contra `demo.trading212.com/api/v0`.
> Actualizado: 2026-05-23. Mantém este ficheiro actualizado quando descobrires
> novos comportamentos inesperados.

---

## 1. Autenticação

```
Header: Authorization: <T212_API_KEY_DEMO>
Base URL: https://demo.trading212.com/api/v0
```

- A chave é uma string opaca (não é Bearer token, não tem prefixo).
- A chave da conta demo é **diferente** da chave da conta real.
- Chaves não têm expiração definida mas podem ser revogadas manualmente.
- Se a chave expirar: todos os endpoints devolvem **401 Unauthorized**.

---

## 2. Portfolio e Posições

### GET /equity/portfolio
Devolve array de posições abertas.

```json
[
  {
    "ticker": "ARM_US_EQ",
    "quantity": 0.4886,
    "averagePrice": 289.74621367,
    "currentPrice": 178.50,
    "ppl": -54.37,
    "fxPpl": -2.10,
    "initialFillDate": "2026-03-12T14:32:00Z",
    "frontend": "WC4",
    "maxBuy": 10.2,
    "maxSell": 0.4886,
    "pieQuantity": 0.0
  }
]
```

**Campos importantes**:
- `quantity` — pode ser fraccionário (ex: 0.4886)
- `ppl` — P&L não realizado em moeda do instrumento (USD para `_US_EQ`)
- `fxPpl` — componente cambial do P&L
- `maxSell` — quantidade máxima que podes vender neste momento
- Se a conta demo for resetada, este endpoint devolve `[]` (array vazio)

### GET /equity/account/cash
```json
{
  "free": 4877.88,
  "total": 5200.00,
  "ppl": -54.37,
  "result": 123.45,
  "invested": 322.12,
  "pieCash": 0.0,
  "blocked": 0.0
}
```

**Campos importantes**:
- `free` — cash disponível para novas ordens (em EUR)
- `total` — equity total da conta
- `ppl` — P&L total não realizado (em EUR)
- `result` — P&L realizado total (em EUR)

**Comportamento conhecido**: a conta demo pode ocasionalmente devolver
`{"free": 0.0, "total": 0.0, ...}` mesmo com posições abertas —
instabilidade conhecida da API demo.

---

## 3. Tickers T212 — Formato e Convenções

Formato: `{SYMBOL}_{MARKET}_{TYPE}`

| Mercado | Sufixo | Exemplo |
|---------|--------|---------|
| NASDAQ/NYSE (USD) | `_US_EQ` | `AAPL_US_EQ`, `ARM_US_EQ` |
| London Stock Exchange (GBP) | `_GBP_EQ` | `VOD_GBP_EQ` |
| London Stock Exchange (GBX) | `_GBX_EQ` | `BP_GBX_EQ` |
| ETF (USD) | `_US_ETF` | `SPY_US_ETF` |

**Tickers opacos** (quando o símbolo não é o ticker yfinance normal):

| T212 | yfinance |
|------|----------|
| `MTEd` | `MU` (Micron) |
| `49Vd` | `VST` (Vistra) |
| `0V6d` | `VRT` (Vertiv) |
| `CJ6d` | `CCJ` (Cameco) |
| `ASMLa` | `ASML.AS` (ASML Euronext) |

**ARM Holdings**: ticker T212 correcto é `ARM_US_EQ` (NASDAQ). Não existe
`ARM_EQ` nem `ARM` sem sufixo.

---

## 4. Ordens de Compra (BUY)

### POST /equity/orders/market
Usado para ordens de mercado fraccionárias.

```json
{
  "ticker": "ARM_US_EQ",
  "quantity": 0.5,
  "timeValidity": "DAY"
}
```

**Comportamento**:
- `quantity` pode ser fraccionária (ex: 0.4886) — T212 suporta
- `timeValidity`: `"DAY"` é o único valor aceite para MARKET
- Resposta inclui `orderId`, `status`, `fillPrice` (pode ser null se ainda não preenchida)
- Durante horário de mercado: executada imediatamente
- Fora de horário: pode ser rejeitada com 400 ou ficar pendente

### POST /equity/orders/limit
Só funciona para quantidades **inteiras**. Para quantidades fraccionárias,
usa sempre MARKET em vez de LIMIT.

```json
{
  "ticker": "ARM_US_EQ",
  "quantity": 1,
  "limitPrice": 175.50,
  "timeValidity": "DAY"
}
```

**AVISO**: `quantity: 0.5` num LIMIT devolve **400 Bad Request**.

---

## 5. Fechar Posições (SELL) — COMPORTAMENTO CRÍTICO

Existem dois mecanismos para fechar uma posição. A escolha depende
da quantidade da posição:

### DELETE /equity/positions/{ticker}
```
DELETE https://demo.trading212.com/api/v0/equity/positions/ARM_US_EQ
```

**Funciona APENAS para posições com quantidade inteira** (ex: 1, 2, 5).

- Quantidade fraccionária (ex: 0.4886): devolve **404 Not Found** mesmo que
  a posição exista. Este é o bug confirmado no FundScope em 2026-05-23.
- Quantidade inteira: fecha toda a posição ao preço de mercado actual.
- Não requer body.

### POST /equity/orders/market (para SELL fraccionário)
Para fechar uma posição fraccionária, usa uma ordem SELL MARKET:

```json
{
  "ticker": "ARM_US_EQ",
  "quantity": 0.4886,
  "timeValidity": "DAY"
}
```

**IMPORTANTE**: a T212 interpreta isto como SELL se já tens a posição.
A `quantity` deve ser **positiva** (não negativa). Enviar quantidade negativa
devolve **400 Bad Request**.

### Lógica correcta para `close_position_demo`
```python
def close_position_demo(ticker: str, quantity: float) -> bool:
    is_fractional = (quantity != int(quantity))  # 0.4886 != 0, True
    if is_fractional:
        # SELL MARKET com quantidade exacta
        resp = _post("/equity/orders/market", {
            "ticker": ticker,
            "quantity": quantity,  # positivo
            "timeValidity": "DAY",
        })
        return resp is not None
    else:
        # DELETE — mais rápido e atómico para inteiros
        return _delete(f"/equity/positions/{ticker}")
```

---

## 6. Rate Limits

- **429 Too Many Requests**: aguardar pelo menos 30 segundos antes de repetir.
- Sem documentar limite exacto de requests/minuto, mas na prática:
  - GET portfolio/cash: seguro até ~10 req/min
  - POST ordens: 1 req por ORDER (espaçar com 1-2s entre ordens)
- O `REQUEST_DELAY_SECONDS` no código deve ser ≥ 1.0

---

## 7. Instabilidade Conhecida da API Demo

A API demo da T212 é intrínsecamente menos estável que a live. Comportamentos
observados:

| Sintoma | Causa provável | Mitigação |
|---------|---------------|------------|
| `GET /equity/portfolio` devolve `[]` | Reset da conta demo ou timeout interno | Retry com backoff |
| `GET /equity/account/cash` devolve `{"free": 0}` com posições abertas | Payload parcial | Verificar `n_positions > 0` antes de abortar |
| `POST /equity/orders/market` demora >10s | Fila de ordens da demo sobrecarregada | Timeout 30s já configurado |
| 503 Service Unavailable | Manutenção planeada ou incidente | Não retente, aguarda ciclo seguinte |

**Nota**: o `positions_ledger.json` é a fonte de verdade LOCAL. Em caso de
conflito com o que a T212 devolve, verificar sempre com `GET /equity/portfolio`.

---

## 8. Respostas de Erro Comuns

| HTTP | Causa | Acção |
|------|-------|--------|
| 400 | Request malformado (quantidade fraccionária em LIMIT, body inválido) | Não retente, corrigir request |
| 401 | Chave API inválida ou expirada | Renovar chave em T212 → Settings → API |
| 404 | Ticker não existe OU posição fraccionária com DELETE | Ver secção 5 |
| 429 | Rate limit atingido | Aguardar 30s |
| 500/503 | Instabilidade demo | Não retente, aguarda ciclo seguinte |

---

## 9. Inspecionar a Conta — Endpoints úteis

```bash
# Posições actuais
curl -H "Authorization: $T212_KEY" \
  https://demo.trading212.com/api/v0/equity/portfolio

# Cash e equity
curl -H "Authorization: $T212_KEY" \
  https://demo.trading212.com/api/v0/equity/account/cash

# Ordens activas (pendentes)
curl -H "Authorization: $T212_KEY" \
  https://demo.trading212.com/api/v0/equity/orders

# Histórico de ordens (paginado)
curl -H "Authorization: $T212_KEY" \
  "https://demo.trading212.com/api/v0/equity/history/orders?limit=50"

# Detalhe de instrumento (confirmar ticker)
curl -H "Authorization: $T212_KEY" \
  https://demo.trading212.com/api/v0/equity/metadata/instruments
```

**Endpoints não disponíveis na API pública T212**:
- Preços em tempo real (usar yfinance/finnhub)
- Dados OHLCV históricos (usar yfinance)
- Websocket de preços (não documentado/suportado na demo)

---

## 10. Métricas da Conta — Cálculo Correcto

### Equity Total (EUR)
```python
equity_eur = cash["free"]
for pos in positions:
    value_native = pos["currentPrice"] * pos["quantity"]
    if "_US_" in pos["ticker"]:  # USD -> EUR
        value_eur = value_native / eurusd_rate
    else:
        value_eur = value_native
    equity_eur += value_eur
```

### P&L Não Realizado (EUR)
```python
# ppl em cash[] já é em EUR (inclui fxPpl)
pnl_unrealised = cash.get("ppl", 0.0)
```

### P&L Realizado
```python
# Dos trades fechados em beta_trades.json
pnl_realised = sum(
    t["result_eur"] for t in trades
    if t.get("closed_at") and t.get("result_eur") is not None
)
```

---

## 11. Histórico de Alterações

| Data | Descoberta |
|------|------------|
| 2026-05-23 | Confirmado: DELETE /equity/positions retorna 404 em posições fraccionárias |
| 2026-05-23 | Confirmado: SELL MARKET com quantity positiva fecha posição fraccionária |
| 2026-05-23 | Confirmado: equity=0 pode ser payload parcial da API demo (cash.free=0 transitório) |
