---
id: readme-fundscope
title: "FundScope — Map of Content"
type: moc
domain: geral
regime: n/a
tags: [moc, readme, root, fundscope]
links_obrigatorios:
  parent_moc: "[[MOC_FundScope]]"
  vizinhos: "[[000-INDEX]] [[CLAUDE]]"
status: stable
ultima_revisao: 2026-05-28
---

# FundScope

[![Trading Bot](https://github.com/chico130/fundscope/actions/workflows/run-trading-bot.yml/badge.svg)](https://github.com/chico130/fundscope/actions/workflows/run-trading-bot.yml)
[![Language](https://img.shields.io/github/languages/top/chico130/fundscope)](https://github.com/chico130/fundscope)
[![License](https://img.shields.io/github/license/chico130/fundscope)](LICENSE)

Sistema de trading automatizado em conta demo Trading 212, com dashboard web em GitHub Pages.

---

## O que é

FundScope é um bot de trading algorítmico que opera sobre a conta demo da Trading 212 (modo BETA). O sistema analisa tickers do mercado americano a cada 15 minutos durante o horário de mercado, gera sinais técnicos, filtra-os com um modelo de machine learning e submete ordens via API REST.

O dashboard web (GitHub Pages) apresenta o estado em tempo real: posições abertas, histórico de trades, equity curve e alertas do sistema.

---

## Arquitectura

```
MERCADO (dados via Finnhub / yfinance)
        │
   phase0.py  ─── ciclo principal (GitHub Actions, cada 15 min)
        │
   strategy.py (Clyde)       ← sinais técnicos: RSI-14, EMA-50/200, volume ratio, ATR
        │
   bonnie.py (Bonnie ML)     ← filtro de qualidade por trade (modelo sklearn, v4-clean)
        │
   cro.py (CRO)              ← gestão de risco sistémico, kill-switch, regime de mercado
        │
   execution.py              ← submete ordens BUY/SELL à Trading 212 API
        │
   notifier.py (Whisper)     ← alertas Telegram (trade executado, oportunidade, diário)
        │
   data/beta/*.json          ← JSONs consumidos pelo frontend
        │
   GitHub Pages              ← dashboard (index.html, portfolio.html, markets.html, …)
```

**Regra de ouro:** os agentes Python escrevem JSONs; o frontend apenas lê. O frontend nunca calcula estado — a Trading 212 API é a única fonte de verdade.

---

## Componentes principais

| Componente | Ficheiro | Responsabilidade |
|---|---|---|
| **Clyde** | `bot/strategy.py` | Geração de sinais técnicos |
| **Bonnie** | `bot/bonnie.py` | Filtro ML por trade (aprovação/bloqueio) |
| **CRO** | `bot/cro.py` | Risco sistémico, regime macro, kill-switch |
| **Whisper** | `bot/notifier.py` | Notificações Telegram |
| **Executor** | `bot/execution.py` | Ordens BUY/SELL via T212 API |
| **Exit Manager** | `bot/exit_manager.py` | Trailing stop, take-profit, saídas parciais |
| **Data Layer** | `bot/data_layer.py` | Resync com T212, enriquecimento técnico |
| **Learner** | `bot/learner.py` | Análise de trades fechados, detecção de padrões |

---

## Stack técnico

| Camada | Tecnologia |
|---|---|
| Linguagem | Python 3.11 |
| Automação | GitHub Actions (cron cada 15 min, dias úteis) |
| Broker API | Trading 212 REST API (demo) |
| Dados de mercado | Finnhub, yfinance |
| Notificações | Telegram Bot API |
| AI Insights | Google Gemini API (`google-genai`) |
| ML | scikit-learn, joblib |
| Sentimento | PRAW (Reddit), VADER |
| Frontend | HTML/CSS/JS estático, GitHub Pages |

---

## Estrutura de pastas

```
fundscope/
├── bot/                     # lógica principal do bot
│   ├── phase0.py            # ciclo de execução (orquestrador)
│   ├── strategy.py          # sinais técnicos (Clyde)
│   ├── bonnie.py            # filtro ML (Bonnie)
│   ├── cro.py               # gestão de risco (CRO)
│   ├── execution.py         # submissão de ordens
│   ├── exit_manager.py      # gestão de saídas
│   ├── notifier.py          # Telegram (Whisper)
│   ├── data_layer.py        # acesso a dados e resync T212
│   ├── api_client.py        # cliente HTTP T212
│   ├── config.py            # parâmetros centrais
│   ├── regime_detector.py   # classificação de regime macro
│   ├── learner.py           # aprendizagem com trades passados
│   ├── backtest.py          # backtesting interno
│   └── …
├── ingest/                  # agentes de actualização de dados
│   ├── update_portfolio.py  # sincroniza data/beta/portfolio.json
│   ├── update_prices.py     # preços para o dashboard
│   ├── update_markets.py    # dados de mercado
│   ├── update_news.py       # feed de notícias
│   └── update_earnings.py   # resultados financeiros
├── scripts/                 # ferramentas de análise e manutenção
│   ├── backtest.py          # backtesting com parâmetros optimizados
│   ├── retrain_bonnie.py    # treino do modelo Bonnie
│   └── validate_pipeline.py # validação do pipeline
├── crawler/                 # crawler de sentimento social
│   ├── runner.py
│   └── sources/
├── data/
│   ├── beta/                # JSONs do bot (lidos pelo dashboard)
│   │   ├── portfolio.json
│   │   ├── beta_trades.json
│   │   ├── beta_positions.json
│   │   ├── beta_equity.json
│   │   ├── cro_insights.json
│   │   └── status.json      # heartbeat LED (last_check, bot_status)
│   ├── cache/               # cache de dados de mercado
│   └── calibration/         # artefactos de calibração do modelo
├── .github/workflows/       # GitHub Actions
│   ├── run-trading-bot.yml  # ciclo principal (cada 15 min)
│   ├── update-portfolio.yml
│   ├── update-markets.yml
│   ├── update-news.yml
│   ├── update-prices.yml
│   └── pages.yml            # deploy GitHub Pages
├── index.html               # dashboard principal
├── portfolio.html           # posições e equity
├── markets.html             # monitor de mercados
├── news.html                # feed de notícias
└── requirements.txt
```

---

## Como correr localmente

### Pré-requisitos

- Python 3.11+
- Conta demo na [Trading 212](https://www.trading212.com/) com API key
- Bot Telegram e chat ID (para notificações)
- Chaves API: Finnhub, Google Gemini (opcional para AI insights)

### Setup

```bash
git clone https://github.com/chico130/fundscope.git
cd fundscope
pip install -r requirements.txt
```

Criar `.env` na raiz com as variáveis necessárias:

```env
T212_API_KEY=<trading212_demo_api_key>
TELEGRAM_BOT_TOKEN=<bot_token>
TELEGRAM_CHAT_ID=<chat_id>
FINNHUB_API_KEY=<finnhub_key>
GEMINI_API_KEY=<gemini_key>          # opcional
```

### Comandos

```bash
# Executar um ciclo manualmente
PYTHONPATH=. python bot/phase0.py

# Backtest standard (4 variantes, parâmetros optimizados)
PYTHONPATH=. python scripts/backtest.py --since 2019-01-01 --use-optimized

# Backtest OOS com Bonnie v4-clean
PYTHONPATH=. python scripts/backtest.py --since 2024-01-01 --use-optimized

# Retreinar modelo Bonnie
PYTHONPATH=. python scripts/retrain_bonnie.py --since 2017-01-01 --model-version v4-clean

# Análise estatística em massa
PYTHONPATH=. python -m bot.mass_backtest

# Servir o dashboard localmente
python serve.py
```

---

## Estado actual — Fase 1 (demo, produção)

O sistema está em execução automática via GitHub Actions sobre a conta demo da Trading 212.

**Modelo activo:** Bonnie v4-clean (`bonnie_model_v4.pkl`)  
**Parâmetros activos:** `atr_stop_mult=1.75`, `atr_tp_mult=4.25`, `max_position_pct=11%`

**Resultados OOS de referência (2024-01-01 → 2026-05-01, run-007):**

| Métrica | Valor |
|---|---|
| Retorno total | +62.2% |
| SPY (benchmark) | +45.2% |
| Alpha | +17.0 pp |
| Sharpe | 2.09 |
| Max drawdown | -10.8% |
| Win rate | 38% |
| R:R | 2.5:1 |
| Profit factor | 1.73 |
| Bonnie filter rate | 34.9% |

**Monitorização activa.** Sem optimizações adicionais até completar 30 dias de validação real.

---

## Documentação

| Ficheiro | Conteúdo |
|---|---|
| [CLAUDE.md](CLAUDE.md) | Diretrizes de arquitectura e regras não-negociáveis |
| [CRO_SPEC.md](docs/CRO_SPEC.md) | Especificação completa do Chief Risk Officer |
| [FASE-1.md](docs/FASE-1.md) | Roadmap e estado da Fase 1 |
| [000-INDEX.md](000-INDEX.md) | Índice completo do vault |
