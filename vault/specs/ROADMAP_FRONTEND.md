# ROADMAP_FRONTEND.md — A Bíblia da Evolução do FundScope

> **Versão:** 1.0 | **Data:** 2026-05-18 | **Autor:** chico130 + Claude
> Roadmap de transformação do frontend FundScope num terminal quantitativo institucional, mantendo o stack vanilla + GitHub Pages.

---

## 0. A Regra de Ouro (Filosofia Arquitetural)

> **Zero dependência de APIs pagas ou com rate-limits no frontend. Toda a inteligência pesada (scraping, LLM, RAG, embeddings) executa em background no servidor via agentes Python. O frontend é estático, ultra-rápido, e limita-se a ler ficheiros JSON limpos.**

### Implicações práticas

1. **Frontend = leitor.** Nunca chama APIs externas em runtime. Lê JSONs versionados no repo.
2. **Servidor = produtor.** Agentes Python (orquestrados por GitHub Actions / cron local) fazem todo o trabalho sujo e publicam JSONs limpos via `git push`.
3. **LLM corre no servidor**, nunca no browser. Outputs são serializados como JSON estruturado.
4. **Falha de um agente não parte o frontend.** Padrão `stale-while-revalidate`: mostrar último JSON válido com flag `stale: true`.

---

## 1. Stack Tecnológico Aprovado

### Frontend (mantém-se minimalista)

| Categoria | Lib | Por quê |
|---|---|---|
| Charting principal | Chart.js (já usado) | Mantém compatibilidade com código existente |
| Sparklines/multi-série pequena | **uPlot** (4KB) | 100× mais rápido que Chart.js para muitas séries pequenas |
| Tabelas ordenáveis | Vanilla JS + delegação de eventos | Não introduzir TanStack — é overkill |
| Full-text search local | **MiniSearch** (~7KB) | Cobre 80% das queries "semânticas" sem LLM |
| SQL no browser (terminal feel) | **DuckDB-WASM** | Query SQL sobre JSON/Parquet locais — atalho-chave |
| Embeddings no browser (opcional, Fase tardia) | **transformers.js** + all-MiniLM | Só para os 5% de queries verdadeiramente semânticas |
| Cache local | IndexedDB (vanilla) | Persistir embeddings/respostas RAG |
| Offline + cache de JSONs | Service Worker | Stale-while-revalidate nativo |

### Backend (agentes Python)

| Categoria | Lib | Por quê |
|---|---|---|
| Dados de mercado | **yfinance** | Já provavelmente em uso; cobre earnings, dividendos, calendar |
| Macro económico | **FRED API** (gratuita, sem rate limit prático) | Yields, M2, liquidez, ISM — substitui scraping de atas |
| SEC Filings | **API oficial `data.sec.gov`** + `sec-edgar-downloader` | É REST gratuita, só pede User-Agent. ZERO scraping necessário |
| News/RSS | **feedparser** + **trafilatura** | Parsing RSS + extração limpa de artigos |
| Scraping de último recurso | **Playwright** + **BeautifulSoup** | Só quando API/RSS não existe |
| Screening de tickers | **finviz-finance** (free, estável) | Antes de scraping próprio para Headhunter |
| Embeddings | **sentence-transformers** (all-MiniLM-L6-v2) | Pré-computar no servidor, exportar JSON |
| Vector search | **FAISS** local ou cosine similarity em numpy | Sem precisar de Pinecone/Weaviate |
| Sumarização LLM | **Claude Haiku 4.5** para batches grandes; **Sonnet 4.6** para análises críticas | Caching agressivo via prompt cache |
| Orquestração | **GitHub Actions cron** (job matrix) ou **APScheduler** local | Free tier de Actions chega para todos os agentes |

### O que rejeitamos (e porquê)

- **React/Vue/Svelte** — quebra a regra "vanilla, ultra-rápido". Não adiciona valor proporcional ao custo.
- **Servidor Node.js permanente** — quebra a regra "GitHub Pages estático". Backends só como **edge functions** (Cloudflare Workers) ou agentes offline.
- **Pinecone/Weaviate/vector DB pago** — FAISS local é suficiente para <100k vetores.
- **APIs pagas de earnings calendar** (Estimize, Earnings Whispers) — `yfinance` + Nasdaq RSS chegam.

---

## 2. Padrões Arquiteturais Obrigatórios

### 2.1 Manifest + Lazy-Load

Em vez de um `watchlist_full.json` gigante (100 tickers × 252 pontos sparkline = 2MB+), o servidor publica:

```
data/watchlist/
├── manifest.json              # Lista de tickers + metadata leve (<10KB)
├── tickers/
│   ├── AAPL.json              # Detalhe pesado por ticker (lazy-load on demand)
│   ├── MSFT.json
│   └── ...
```

O frontend carrega o `manifest.json` no boot, e busca o `tickers/<X>.json` só quando o utilizador interage com essa linha.

### 2.2 Stale-While-Revalidate

Cada JSON produzido pelo agente inclui frontmatter:

```json
{
  "_meta": {
    "generated_at": "2026-05-18T18:00:00Z",
    "agent_version": "headhunter@1.2.0",
    "stale": false,
    "expires_at": "2026-05-18T22:00:00Z",
    "fallback_used": false
  },
  "data": { ... }
}
```

Se o agente falha, mantém-se o JSON anterior + `stale: true` + banner no frontend.

### 2.3 Schema Versioning

Cada JSON declara `schema_version`. O frontend valida e degrada graciosamente se desatualizado.

### 2.4 Web Workers para Processamento Pesado

Parsing de JSONs >500KB, cosine similarity, DuckDB queries — sempre em Worker para não bloquear UI.

---

## 3. Roadmap por Aba

---

### 3.1 Aba **WATCHLIST**

#### Fase 1 — Sincronização & Login ✅ (Concluído — commit fe4838a)

#### Fase 2 — Tabela Quantitativa
- **Objetivo:** Tabela ordenável com sparklines (Momentum 30d, Volatilidade 30d, Bonnie Score, Fator de Risco CRO).
- **Agente Python:** `agents/watchlist_enricher.py` — corre diariamente, lê `data/watchlist/raw.json`, enriquece com:
  - Sparkline arrays (preço normalizado, 30 pontos)
  - Bonnie score histórico (lido de `logs/bonnie_decisions.jsonl`)
  - Fator de risco CRO (lido de `data/beta/cro_insights.json`)
  - Momentum, RSI-14, vol-30d
- **Output:** `data/watchlist/manifest.json` + `data/watchlist/tickers/<X>.json` (padrão Manifest).
- **Frontend:** `watchlist.html` com tabela vanilla + uPlot para sparklines inline.
- **Libs novas:** uPlot.
- **Estimativa:** 1 sprint.

#### Fase 3 — Agente "Headhunter"
- **Objetivo:** Sugerir 5–10 tickers/semana com base em regime macro + estilo Bonnie.
- **Estratégia recomendada (em vez de scraping do zero):**
  1. **finviz-finance** com presets de screening (gratuito, estável)
  2. Filtros macro-aware: se regime = `risk_off` → defensive/dividend; se `risk_on` → momentum/growth
  3. Score híbrido: finviz score + alinhamento com regras Bonnie
  4. **Só usar Playwright** se finviz não cobrir caso específico (último recurso)
- **Agente:** `agents/headhunter.py` — corre semanalmente, publica `data/watchlist/suggestions.json`.
- **Frontend:** card "Sugestões da Semana" no topo do watchlist com botão "Adicionar".
- **Risco mitigado:** flag `stale` + último snapshot retido se scraper partir.
- **Estimativa:** 2 sprints.

---

### 3.2 Abas **STOCK / DETAIL / SEARCH**

#### Fase 1 — AI Insights Widget ✅ (Concluído — commit fd269b4)

#### Fase 2 — Search Quantitativo em 3 Camadas

**Estratégia híbrida (rejeitamos LLM puro):**

**Camada A (cobre ~80% das queries) — Facets sobre dados estruturados**
- Frontend mantém índice de campos: `{ticker, sector, bonnie_score, cro_status, momentum, last_pe, ...}`
- Queries como "tech aprovadas pela Bonnie" → filtros declarativos.
- Lib: vanilla JS + DuckDB-WASM para queries SQL complexas (`SELECT * WHERE sector='Tech' AND bonnie_score > 0.7`).

**Camada B (cobre ~15%) — Full-text fuzzy**
- MiniSearch sobre `display_name`, `description`, `reason` dos trades.
- Queries como "ações com narrativa AI" sem precisar de embeddings.

**Camada C (apenas ~5%) — Embeddings semânticos**
- **Pré-computados no servidor** com sentence-transformers (all-MiniLM-L6-v2).
- Exportados como `data/search/embeddings.json` (~5MB para 500 tickers).
- Cosine similarity no browser via Web Worker.
- Só ativada quando A+B falharem.

**Agente:** `agents/search_indexer.py` — gera os 3 índices, corre diariamente.
**Frontend:** `search.html` com barra única, escolha automática da camada apropriada.
**Estimativa:** 2 sprints (1 por A+B, 1 por C).

#### Fase 3 — Terminal RAG (Pre-Baked + Edge)

**Não tentar RAG ao vivo em GitHub Pages.** Abordagem em 2 caminhos:

**Caminho 1 — Pre-Baked FAQ (alta confiabilidade)**
- Agente `agents/rag_baker.py` corre noturnamente:
  - Pega numa lista de ~100 perguntas comuns ("Qual o melhor trade da Bonnie este mês?", "Quantas vezes o CRO acionou kill-switch?", "Que setores tiveram maior win-rate?")
  - Para cada uma, faz RAG sobre `data/beta/beta_trades.json`, `cro_insights.json`, `logs/bonnie_decisions.jsonl` usando Claude Sonnet 4.6
  - Publica `data/rag/faq.json` com pares pergunta+resposta+fontes
- Frontend faz fuzzy match (MiniSearch) sobre as perguntas e mostra resposta + links às fontes.
- **Custo: ~$0.50/dia** (100 queries com cache).

**Caminho 2 — Live RAG via Edge Function (queries inéditas)**
- Quando fuzzy match falha (<70% similaridade), botão "Perguntar à IA" → POST para Cloudflare Worker
- Worker chama Anthropic API com:
  - Claude Haiku 4.5 para queries simples (factual, contagem)
  - Claude Sonnet 4.6 para queries analíticas
  - Prompt cache agressivo sobre os JSONs de contexto
- Free tier Cloudflare: 100k req/dia, suficiente para uso pessoal.

**Estimativa:** 3 sprints.

---

### 3.3 Aba **MARKETS**

#### Fase 1 — Dashboard Macro
- **Objetivo:** Visão one-glance do mercado.
- **Métricas:** SPY/QQQ/IWM YTD + 1D, VIX, DXY, US10Y, Market Breadth (% acima MA200), Fear & Greed Index.
- **Agente:** `agents/macro_snapshot.py` — corre a cada hora.
- **Fontes:** yfinance (índices), **FRED API** (yields, M2), CNN Fear & Greed scraping (1 fonte, baixo risco).
- **Output:** `data/markets/macro.json`.
- **Frontend:** `markets.html` com grid de KPIs + uPlot sparklines.
- **Estimativa:** 1 sprint.

#### Fase 2 — Heatmap de Correlação Setorial
- **Objetivo:** Matriz 11×11 de correlações entre setores SPDR (XLK, XLF, XLE, ...) baseada em janela 60d.
- **Bónus FundScope:** sobrepor com volatilidade dos tickers da watchlist por setor — vê risco concentrado.
- **Agente:** `agents/sector_correlation.py` — corre diariamente.
- **Output:** `data/markets/correlation.json` (matriz 11×11 + watchlist exposure).
- **Frontend:** heatmap vanilla SVG (não precisa de Plotly).
- **Estimativa:** 1 sprint. **High visual impact, low effort — fazer cedo.**

#### Fase 3 — Agente Macro Cognitivo
- **Objetivo:** Sumário diário "estado da liquidez" em linguagem natural.
- **Estratégia (rejeitamos scraping de atas Fed):**
  - **Fonte primária: FRED API** — séries de liquidez (M2, RRP, BTFP, Net Liquidity)
  - **Fonte secundária:** SEC API para 8-K filings recentes de grandes bancos
  - **LLM:** Sonnet 4.6 lê deltas das séries + headlines do dia, produz sumário 200-palavras com semáforo (Verde/Amarelo/Vermelho).
- **Agente:** `agents/macro_analyst.py` — corre 1×/dia (mercados abertos).
- **Output:** `data/markets/macro_analysis.json` com `traffic_light`, `summary`, `key_drivers`, `sources`.
- **Frontend:** card destacado no topo da Markets tab.
- **Custo:** ~$0.10/dia.
- **Estimativa:** 2 sprints.

---

### 3.4 Abas **NEWS / EARNINGS**

#### Fase 1 — Agregador RSS
- **Objetivo:** Feed unificado de news financeiras.
- **Fontes:** Bloomberg RSS, Reuters Business, Yahoo Finance, Investing.com, SeekingAlpha (tier gratuito), MarketWatch.
- **Agente:** `agents/news_aggregator.py` — corre a cada 15min via Actions.
- **Stack:** feedparser → trafilatura (limpar HTML) → dedup por título + URL.
- **Filtro inteligente:** só publica artigos onde algum ticker da watchlist é mencionado (regex + alias map).
- **Output:** `data/news/feed.json` (últimos 200 itens).
- **Frontend:** `news.html` com lista virtualizada + filtros por ticker.
- **Estimativa:** 1 sprint.

#### Fase 2 — Earnings Calendar
- **Estratégia (mais simples do que sugerias):**
  - **Fonte primária:** `yfinance.Ticker(x).calendar` — já dá earnings date para tickers da watchlist
  - **Fonte secundária:** Nasdaq RSS feed de earnings (gratuito, estável)
  - **Playwright apenas como fallback** se faltar info
- **Agente:** `agents/earnings_calendar.py` — corre 2×/dia.
- **Output:** `data/earnings/calendar.json` com next 30 dias por ticker.
- **Cross-link:** Bonnie já tem `no_trade_before_earnings_days=2` — frontend mostra badge "⚠️ Earnings em X dias" nas posições afetadas.
- **Frontend:** `earnings.html` (já existe; enriquecer) — agenda + alertas.
- **Estimativa:** 1 sprint.

#### Fase 3 — Analista de Sentimento SEC
- **Objetivo:** Semáforo Verde/Amarelo/Vermelho sobre guidance + risk factors dos 10-K/10-Q dos tickers em carteira.
- **Estratégia (REJEITAR scraping — usar API SEC oficial):**
  - **Fonte:** `data.sec.gov` (REST oficial, gratuita, sem rate limit prático com User-Agent)
  - **Lib:** `sec-edgar-downloader` para 10-K/10-Q completos
  - **Chunking:** extrair só secções relevantes (Item 1A Risk Factors, Item 7 MD&A, forward-looking statements)
  - **LLM pipeline:**
    1. Haiku 4.5 chunka e classifica sentiment por secção (barato)
    2. Sonnet 4.6 sintetiza relatório final + traffic light (chamada única por ticker)
  - **Cache:** filings são imutáveis após publicação — cache permanente em disco. **1 chamada LLM por filing, vida toda.**
- **Agente:** `agents/sec_analyst.py` — corre quando há novo filing detetado (event-driven via SEC RSS).
- **Output:** `data/earnings/sec_analysis/<ticker>.json` com `traffic_light`, `summary`, `red_flags`, `guidance_tone`, `filing_url`, `analyzed_at`.
- **Frontend:** badge colorido nas tabelas + drill-down com análise completa.
- **Risco mitigado:** filings grandes (200+pg) tratados com chunking obrigatório.
- **Estimativa:** 3 sprints.

---

## 4. Ordem de Execução Recomendada

Esta ordem maximiza valor visível precoce + reaproveita infraestrutura:

| # | Sprint | Item | Razão |
|---|---|---|---|
| 1 | S1 | Watchlist Fase 2 (tabela quantitativa + sparklines) | Visual impact imediato, padrão Manifest fica estabelecido |
| 2 | S2 | Markets Fase 1 (dashboard macro) | Reusa padrão Manifest, fonte FRED simples |
| 3 | S3 | News Fase 1 (agregador RSS) | Cria base para enriquecimento de news em tickers |
| 4 | S4 | Earnings Fase 2 (calendar) | Liga-se à watchlist + às regras Bonnie existentes |
| 5 | S5 | Markets Fase 2 (heatmap correlação) | Alto impacto visual, baixo esforço |
| 6 | S6-7 | Search Fase 2 (3 camadas) | Habilita a "feel" de terminal |
| 7 | S8-10 | Search Fase 3 (RAG pre-baked + edge) | A "matadora" — converte projeto em terminal real |
| 8 | S11-12 | Watchlist Fase 3 (Headhunter) | Construído sobre Search + Macro já existentes |
| 9 | S13-14 | Markets Fase 3 (Macro Analyst) | Reutiliza pipeline LLM do RAG |
| 10 | S15-17 | Earnings Fase 3 (SEC Analyst) | Mais complexo, faz sentido por último |

---

## 5. Métricas de Sucesso

Cada fase só é "Done" quando:

- ✅ Agente Python tem teste (pelo menos smoke test)
- ✅ JSON output tem schema versionado + `_meta` block
- ✅ Frontend degrada graciosamente se JSON faltar/estiver `stale`
- ✅ Agente está agendado em GitHub Actions cron + tem alerta Telegram em caso de falha (via `bot/notifier.py` existente)
- ✅ Tempo de carregamento da página afetada permanece <500ms (medir com Lighthouse)
- ✅ Documentação do agente em `agents/<name>/README.md`

---

## 6. Riscos & Mitigações

| Risco | Probabilidade | Mitigação |
|---|---|---|
| Scraping quebra com mudança de HTML | Alta | Padrão stale-while-revalidate; alerta Telegram; preferir APIs oficiais (SEC, FRED, yfinance) |
| JSONs ficam gigantes | Média | Padrão Manifest + lazy-load + gzip (GitHub Pages serve automático) |
| Custo LLM explode | Média | Caching agressivo (filings = cache permanente); Haiku para 80%; Sonnet só para análise final |
| RAG dá respostas erradas | Alta | Pre-baked FAQ tem human review; edge function inclui sempre "fontes" no output; nunca usar LLM para decisões de trading |
| Cloudflare Worker excede free tier | Baixa | Fallback automático para pre-baked FAQ |
| Frontend lento com muitos dados | Média | Web Workers para parsing + cosine; uPlot em vez de Chart.js para sparklines; virtualization em listas |
| GitHub Actions cron falha silenciosamente | Média | Watchdog que monitora `_meta.generated_at` — se >2× expected interval, alerta |

---

## 7. Princípios Não-Negociáveis

1. **JSON é a única lingua franca entre servidor e frontend.** Nunca chamadas diretas a APIs no browser.
2. **Falha de um agente nunca parte o frontend.** Stale data > broken UI.
3. **LLM nunca toma decisões de trading.** Só sumariza, classifica, ou responde. Bonnie/CRO mantêm autoridade.
4. **Vanilla > Framework.** Cada KB adicionado precisa de justificação 10× o valor.
5. **Cache é arquitetura, não otimização.** Pensar cache desde o primeiro draft de cada agente.
6. **Documentação não é opcional.** Cada JSON tem schema documentado; cada agente tem README.

---

## 8. Glossário

- **Agente** — Script Python no `agents/` que produz um JSON limpo. Tem schema, README, alerta Telegram.
- **Manifest** — JSON leve (<10KB) que lista entidades + metadata mínima, com ponteiros para JSONs detalhados.
- **Stale** — JSON cujo `expires_at` passou mas que ainda é mostrado por o agente atual ter falhado.
- **Pre-baked** — Resposta LLM gerada em batch noturno e servida estaticamente.
- **Edge function** — Cloudflare Worker (free tier) usado *apenas* para queries que não cabem em pre-baked.

---

## 9. Próximos Passos Imediatos

1. [ ] Criar pasta `agents/` na raiz com estrutura `agents/<nome>/{agent.py, README.md, schema.json, test.py}`
2. [ ] Criar `data/_meta/manifest_schema.json` com o schema do bloco `_meta` partilhado
3. [ ] Criar `agents/watchlist_enricher.py` (Sprint 1 — primeira fase a executar)
4. [ ] Adicionar workflow `.github/workflows/agents.yml` com cron matrix
5. [ ] Atualizar `bot/notifier.py` para receber alertas dos novos agentes via canal Telegram dedicado

---

**Este documento é vivo.** Atualizar quando:
- Uma fase for concluída (marcar com ✅ + link para commit)
- Uma decisão arquitetural mudar (manter changelog no fundo)
- Uma nova lib/abordagem provar valor (adicionar à secção 1)