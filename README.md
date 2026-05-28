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

# FundScope — Map of Content

> Hub de navegação central do projeto. Cada secção liga aos ficheiros vitais da arquitetura.
> Índice completo do vault (todas as camadas): [000-INDEX](000-INDEX.md).

---

## Visão Geral

O FundScope é um sistema de trading automatizado + dashboard web alojado em GitHub Pages.  
Opera em duas camadas paralelas:

- **ALFA** — Conta real ISA/Invest (Trading212 live), gerida manualmente.
- **BETA** — Bot automatizado em paper trading (Trading212 demo), controlado pela tríade Clyde / Bonnie / CRO.

---

## Arquitetura do Bot

```
   MERCADO
      │
 [[regime_detector.py]]          ← Classifica o regime macro (bull/bear)
      │
 [[cro.py]] — Chief Risk Officer ← Aprova o estado do sistema
    ╱          ╲
[[bonnie.py]]  [[strategy.py]]   ← Filtro de risco / Geração de sinais (Clyde)
    ╲          ╱
  [[execution.py]]               ← Submete ordens à Trading212
      │
  [[api_client.py]]              ← Cliente HTTP da T212 API
```

---

## 1. Motor de Trading — Clyde

| Ficheiro | Responsabilidade |
|---|---|
| [[strategy.py]] | Geração de sinais técnicos (RSI-14, EMA-50/200, volume ratio) |
| [[execution.py]] | Submissão de ordens BUY/SELL à API da Trading212 |
| [[api_client.py]] | Cliente HTTP para a T212 API (demo e live) |
| [[exit_manager.py]] | Gestão de saídas e trailing stops |
| [[price_feed.py]] | Feed de preços em tempo real |
| [[feature_builder.py]] | Construção de features para os modelos |

---

## 2. Gestão de Risco — Bonnie & CRO

| Ficheiro | Responsabilidade |
|---|---|
| [[bonnie.py]] | Filtro de risco por trade individual (aprovação/bloqueio) |
| [[cro.py]] | Chief Risk Officer — risco sistémico, insights cognitivos, kill-switch |
| [[regime_detector.py]] | Classificação do regime de mercado macro |
| [[CRO_SPEC.md]] | Especificação completa do CRO (Fases 0, 1, 2) |

---

## 3. Pipeline Principal

| Ficheiro | Responsabilidade |
|---|---|
| [[phase0.py]] | Ciclo de observação — lê dados, calcula técnicos, não executa ordens |
| [[main.py]] | Orquestrador do ciclo de execução live |
| [[watchdog.py]] | Monitor de saúde do processo do bot |
| [[watchlist_manager.py]] | Gestão dinâmica da watchlist de tickers |

---

## 4. Aprendizagem & Memória

| Ficheiro | Responsabilidade |
|---|---|
| [[learner.py]] | Análise de trades fechados, deteção de padrões de erro |
| [[model_trainer.py]] | Treino de modelos de machine learning |
| [[evaluate_bonnie.py]] | Avaliação de performance da Bonnie |
| [[backtest.py]] | Backtesting de estratégias |
| [[mass_backtest.py]] | Análise estatística em massa (`python -m bot.mass_backtest`) |
| [[position_ledger.py]] | Registo de posições e histórico de P&L |

---

## 5. Infraestrutura & Suporte

| Ficheiro | Responsabilidade |
|---|---|
| [[config.py]] | Configuração central: parâmetros de risco, API keys, paths |
| [[data_layer.py]] | Acesso a dados de portfolio e enriquecimento técnico |
| [[notifier.py]] | Notificações Telegram (alertas, relatórios diários, insights CRO) |
| [[reporter.py]] | Geração de relatórios periódicos |
| [[logger.py]] | Logging de decisões e erros |

---

## 6. Dashboard Web

O frontend é uma SPA estática servida via GitHub Pages.

- `index.html` — Página principal
- `portfolio.html` — Portfólio ALFA + BETA lado a lado
- `markets.html` — Monitor de mercados
- `news.html` — Feed de notícias

Dados consumidos pelo dashboard:
- `data/alpha/portfolio.json` — Estado da conta real ALFA
- `data/beta/beta_summary.json` — KPIs do bot BETA
- `data/beta/beta_positions.json` — Posições abertas
- `data/beta/beta_equity.json` — Curva de equity
- `data/beta/beta_trades.json` — Diário de trades com contexto técnico
- `data/beta/cro_insights.json` — Insights cognitivos do CRO

---

## 7. Documentação

| Ficheiro | Conteúdo |
|---|---|
| [[CLAUDE.md]] | Diretrizes para o Claude Code — regras de infraestrutura e comandos |
| [[CRO_SPEC.md]] | Especificação completa do Chief Risk Officer (3 fases) |
| [[FUNDSCOPE_CLAUDE_CODE_SPEC.md]] | Especificação técnica global do projeto |
| [[FASE-1.md]] | Roadmap de evolução e diagnóstico do bot |
| [[GRAPH_REPORT.md]] | Grafo de dependências gerado pelo Graphify |
| [[EARNINGS_TAB.md]] | Especificação do separador de resultados financeiros |

---

## Comandos Rápidos

```bash
# Execução manual do pipeline
python bot/phase0.py

# Análise estatística / backtest em massa
python -m bot.mass_backtest

# Atualizar o grafo de conhecimento
/graphify .

# Validar sintaxe da Bonnie
python -c "import ast; ast.parse(open('bot/bonnie.py').read())"
```