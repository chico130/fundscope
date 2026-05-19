---
id: moc-frontend
title: "MOC — Frontend (Dashboard Web SPA)"
type: moc
domain: frontend
regime: n/a
tags: [moc, frontend, dashboard, spa, github-pages, pwa]
links_obrigatorios:
  parent_moc: "[[MOC_FundScope]]"
  vizinhos: "[[MOC_Infraestrutura]] [[MOC_CRO]] [[MOC_Bonnie]]"
status: stable
ultima_revisao: 2026-05-19
---

# MOC — Frontend (Dashboard Web SPA)

> SPA estática servida via GitHub Pages. Lê JSONs do repositório — nunca escreve no bot.

Hub: [[MOC_FundScope]] → este MOC → páginas e contratos de dados.

**Regra de Ouro:** Agentes Python cospem JSONs. Frontend só lê. Nunca o inverso.

Spec completa: [[ROADMAP_FRONTEND]]

---

## Páginas da SPA

| Ficheiro | Função | Dados consumidos |
|---|---|---|
| `index.html` | Entry point / redirect | — |
| `portfolio.html` | Portfolio ALFA + BETA lado a lado | portfolio.json, beta_*.json |
| `markets.html` | Monitor de mercados e sectores | markets.json, data.json |
| `news.html` | Feed de notícias com impacto | news.json |
| `search.html` | Pesquisa de ações (3-layer RAG) | data.json, data/beta/ |
| `stock.html` | Perfil detalhado de ticker | data.json, data/beta/watchlist_fundamentals.json |
| `earnings.html` | Calendário de resultados financeiros | earnings.json, [[MOC_Frontend|beta_positions.json]] |
| `live_portfolio.html` | Portfolio live (deprecated redirect) | — |

---

## Contratos de Dados (JSON — produzidos pelo bot, lidos pelo frontend)

| JSON | Produtor | Frequência |
|---|---|---|
| `data.json` | update_prices.py | 30 min (mercado aberto) |
| `portfolio.json` | update_portfolio.py | 2x/dia |
| `markets.json` | update_markets.py | 3x/dia |
| `news.json` | update_news.py | 1x/dia |
| `earnings.json` | update_earnings.py | 1x/dia |
| `data/beta/beta_summary.json` | bot/phase0.py | por ciclo |
| `data/beta/beta_positions.json` | bot/phase0.py | por ciclo |
| `data/beta/cro_insights.json` | [[cro|cro.py]] | por ciclo |

---

## Autenticação ([[serve|serve.py]])

Para desenvolvimento local, `serve.py` expõe uma API com auth:
- `/api/portfolio` — dados ALFA protegidos
- `/api/beta/*` — whitelist de ficheiros beta
- Token de sessão (7 dias) via `data/user_credentials.json`

Ver [[SPEC_HANDOFF_SONNET]] para spec completa do auth + authFetch helper.

---

## PWA e Assets

| Asset | Papel |
|---|---|
| `manifest.json` | PWA config (nome, ícones, tema teal #01696f) |
| `sw.js` | Service Worker (cache offline) |
| `favicon.svg` | Ícone piggy bank SVG |
| `icon-192.png` / `icon-512.png` | PWA icons |

---

## [[MOC_Frontend|GitHub Actions]] — Atualização Automática

| Workflow | Trigger | Script |
|---|---|---|
| update.yml | A cada 30 min (sessão US) | update_prices.py → data.json |
| update-portfolio.yml | 2x/dia | update_portfolio.py → portfolio.json |
| update-markets.yml | 3x/dia | update_markets.py → markets.json |
| update-news.yml | 1x/dia | update_news.py → news.json |
| run-trading-bot.yml | A cada 15 min (sessão US) | bot/phase0.py |

---

## Ligações Cruzadas

- [[MOC_Infraestrutura]] — [[serve|serve.py]], manifest.json, sw.js são infra de suporte
- [[MOC_CRO]] — [[MOC_CRO|cro_insights.json]] alimenta o painel de insights do portfolio.html
- [[MOC_Bonnie]] — [[MOC_Bonnie|beta_trades.json]] e [[MOC_Bonnie|bonnie_log.json]] são consumidos pelo frontend
- [[ROADMAP_FRONTEND]] — regras de ouro, stale-while-revalidate, tech stack
- [[EARNINGS_TAB]] — spec do separador de resultados
