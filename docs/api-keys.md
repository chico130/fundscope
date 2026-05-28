# API Keys — Capacidades e Limites

> Este ficheiro documenta as APIs externas usadas pelo FundScope: variáveis de ambiente, capacidades, limites conhecidos e onde cada chave é usada no código.
> **Nenhuma chave real deve ser colocada aqui.** Usar sempre GitHub Actions Secrets ou ficheiro `.env` local (não versionado).

---

## Trading 212 Demo API

- **Documentação oficial:** https://docs.trading212.com/api/
- **Base URL:** `https://demo.trading212.com/api/v0`

### Variáveis de ambiente

| Variável | Função |
|---|---|
| `T212_API_ID` | ID da chave (prefixo numérico) — obrigatório para auth Basic |
| `T212_API_KEY` | Secret da chave — obrigatório para auth Basic |
| `T212_LIVE_API_ID` | ID para conta live (reservado para Fase 3 — não usado em runtime) |
| `T212_LIVE_API_KEY` | Secret para conta live (reservado para Fase 3 — não usado em runtime) |

**Esquema de autenticação:** `Authorization: Basic base64(T212_API_ID:T212_API_KEY)`
A chave isolada (sem ID) devolve sempre 401. Confirmado empiricamente contra `demo.trading212.com`.

### Capacidades

- Leitura de posições abertas no portfolio demo (`GET /portfolio`)
- Leitura do saldo disponível (`GET /cash`)
- Colocação de ordens de mercado (`POST /equity/orders/market`) — BUY
- Cancelamento de ordens (`DELETE /equity/orders/{id}`) — SELL
- Listagem de ordens activas (`GET /equity/orders`)
- Listagem de instrumentos disponíveis (`GET /equity/instruments`)

### Limites conhecidos

- **Rate limit demo:** ~1 req/s (bot usa `REQUEST_DELAY_SECONDS = 1.2` s entre chamadas)
- **Resposta 429:** bot aguarda 30 s antes de retry
- **Ordens LIMIT:** não aceitam frações de acção nem preços com mais de 2 casas decimais
- **Histórico de preços e candlesticks:** não exposto na API pública — bot usa yfinance como fallback

### Como obter / renovar

1. Criar conta em https://www.trading212.com/ e activar a conta demo
2. Em `Settings → API (Beta)` gerar um par ID + Secret
3. Copiar ambos para os Secrets do repositório (`T212_API_ID`, `T212_API_KEY`) ou para o ficheiro `.env` local

### Onde é usada no código

| Ficheiro | Função / contexto |
|---|---|
| [bot/config.py](../bot/config.py) | Leitura das env vars, construção do header `T212_DEMO_KEY` |
| [bot/api_client.py](../bot/api_client.py) | Todas as chamadas HTTP a T212 (GET, POST, DELETE) |
| [bot/execution.py](../bot/execution.py) | `execute_trade()`, `execute_exit()` |
| [bot/data_layer.py](../bot/data_layer.py) | `get_full_portfolio_state()`, `_try_t212_sync()` |
| [ingest/update_portfolio.py](../ingest/update_portfolio.py) | Sync do `portfolio.json` e `beta_positions.json` |
| [t212_debug.py](../t212_debug.py) | Script de diagnóstico de conectividade |

**Última verificação de limites:** 2026-05-29
> Estes limites podem mudar — verificar na documentação oficial antes de escalar.

---

## Finnhub

- **Documentação oficial:** https://finnhub.io/docs/api
- **Base URL:** `https://finnhub.io/api/v1`

### Variáveis de ambiente

| Variável | Função |
|---|---|
| `FINNHUB_API_KEY` | Token de autenticação (formato query param `?token=...`) |
| `FINNHUB_TOKEN` | Alias aceite pelo bot (o `.env` do VPS usa este nome) |

O bot aceita qualquer um dos dois nomes — lê `FINNHUB_API_KEY` primeiro, com fallback para `FINNHUB_TOKEN`.

### Capacidades

- Cotações em tempo real para acções US e internacionais (`/quote`)
- Notícias de mercado gerais (`/news`)
- Notícias por empresa (`/company-news`)
- Recomendações de analistas (`/stock/recommendation`)
- Sentimento de mercado e social

### Limites conhecidos

- **Free tier:** 60 req/min (1 req/s)
- **Bot throttle:** 1 req/s (`_MIN_INTERVAL = 1.05` s) → ~57 req/min com margem de segurança
- **Resposta 429:** exponential backoff (1 s → 2 s → 4 s); após esgotar retries, regista falha no circuit breaker
- **Quota diária free tier:** não documentada explicitamente — monitorizar 429s em produção
- **Dados históricos OHLCV:** disponíveis no plano pago; free tier usa yfinance como substituto

### Como obter / renovar

1. Registo gratuito em https://finnhub.io/register
2. O token é gerado automaticamente e visível em `Dashboard → API Key`
3. Copiar para o Secret `FINNHUB_TOKEN` (GitHub Actions) ou para `.env` local

### Onde é usada no código

| Ficheiro | Função / contexto |
|---|---|
| [bot/config.py](../bot/config.py) | Leitura e exposição de `FINNHUB_API_KEY` |
| [bot/price_feed.py](../bot/price_feed.py) | `get_quote()` — feed primário de cotações em tempo real |
| [ingest/update_portfolio.py](../ingest/update_portfolio.py) | `_fh_get()` — enriquecimento de posições com dados Finnhub |
| [ingest/update_markets.py](../ingest/update_markets.py) | Sentimento de mercado para `markets.json` |
| [ingest/update_news.py](../ingest/update_news.py) | `fetch_finnhub()`, `fetch_finnhub_company()` — notícias gerais e por empresa |
| [crawler/sources/finnhub_analysts.py](../crawler/sources/finnhub_analysts.py) | Recomendações de analistas para o crawler |

**Última verificação de limites:** 2026-05-29
> Estes limites podem mudar — verificar na documentação oficial antes de escalar.

---

## Telegram Bot API

- **Documentação oficial:** https://core.telegram.org/bots/api
- **Base URL:** `https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}`

### Variáveis de ambiente

| Variável | Função |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token do bot criado via @BotFather |
| `TELEGRAM_CHAT_ID` | ID do chat / utilizador destinatário das mensagens |

### Capacidades

- Envio de mensagens de texto para um chat específico (`/sendMessage`)
- Notificações silenciosas (flag `disable_notification`)
- Não é usada nenhuma funcionalidade bidirecional (o bot não lê mensagens)

### Limites conhecidos

- **Rate limit por chat:** 1 msg/s (Telegram impõe throttle automático)
- **Rate limit global por bot:** 30 msg/s
- **Tamanho máximo de mensagem:** 4096 caracteres
- **Retry no bot:** 3 tentativas com intervalo de 5 s em falhas de rede
- Rejeições de API (token ou chat_id inválidos) não são retentadas
- Falhas são sempre silenciadas — nunca abortam o ciclo principal (R3)

### Como obter / renovar

1. Abrir conversa com [@BotFather](https://t.me/BotFather) no Telegram
2. Usar o comando `/newbot` e seguir as instruções
3. O `TELEGRAM_BOT_TOKEN` é devolvido no final
4. Para obter o `TELEGRAM_CHAT_ID`: iniciar conversa com o bot e aceder a `https://api.telegram.org/bot{TOKEN}/getUpdates`
5. Copiar ambos para os Secrets do repositório

### Onde é usada no código

| Ficheiro | Função / contexto |
|---|---|
| [bot/notifier.py](../bot/notifier.py) | `enviar_alerta()`, `enviar_trade_executada()`, `enviar_oportunidade()` — alertas de trades e oportunidades |
| [bot/watchdog.py](../bot/watchdog.py) | SOS de emergência quando `EMERGENCY_LOCK.txt` é criado |
| [ingest/update_portfolio.py](../ingest/update_portfolio.py) | `_send_telegram_alert()` — alertas de sincronização de portfolio |

**Última verificação de limites:** 2026-05-29
> Estes limites podem mudar — verificar na documentação oficial antes de escalar.

---

## Google Gemini API

- **Documentação oficial:** https://ai.google.dev/gemini-api/docs
- **SDK Python:** `google-genai` (`pip install google-genai`)
- **Modelo usado:** `gemini-2.0-flash-lite`

### Variáveis de ambiente

| Variável | Função |
|---|---|
| `GEMINI_API_KEY` | Chave de API do Google AI Studio |

### Capacidades

- Geração de texto e JSON estruturado via `client.models.generate_content()`
- **Uso 1 — AI Insights (serve.py):** análise de contexto de mercado por ticker — devolve JSON com `sentiment`, `history`, `social`
- **Uso 2 — Symbol Resolver (ingest/update_portfolio.py):** resolução de tickers T212 (opacos) para símbolos yfinance/Finnhub standard

### Configuração de chamada

```
model:             gemini-2.0-flash-lite
temperature:       0.4
max_output_tokens: 1500
timeout:           20 000 ms (hard ceiling)
response_mime_type: application/json
```

### Limites conhecidos

- **Free tier (AI Studio):** 15 req/min, 1 500 000 tokens/min, 1 500 req/dia
- **Rate limit local (serve.py):** sliding window por IP — configurável via `_AI_RATE_MAX_RPM` e `_AI_RATE_WINDOW_S`
- **Cache de insights:** TTL de 8 h (`AI_INSIGHTS_TTL_H`); máximo de 72 h antes de considerar stale
- Falhas são silenciadas — retornam `None` sem abortar o ciclo ou o servidor

### Como obter / renovar

1. Aceder a https://aistudio.google.com/apikey
2. Gerar uma nova chave de API
3. Copiar para o Secret `GEMINI_API_KEY` (GitHub Actions) ou para `.env` local

### Onde é usada no código

| Ficheiro | Função / contexto |
|---|---|
| [serve.py](../serve.py) | `_call_gemini_insight()` — geração de resumos de mercado por ticker no dashboard |
| [ingest/update_portfolio.py](../ingest/update_portfolio.py) | `gemini_resolve_symbol()` — resolução de tickers T212 opacos para símbolos canónicos |

**Última verificação de limites:** 2026-05-29
> Estes limites podem mudar — verificar na documentação oficial antes de escalar.
