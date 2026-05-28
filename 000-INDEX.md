---
id: index-fundscope
title: "000 — Índice do Vault FundScope"
type: moc
domain: geral
regime: n/a
tags: [moc, index, root, fundscope, navegacao]
links_obrigatorios:
  parent_moc: "self"
  vizinhos: "[[MOC_FundScope]] [[README]] [[CLAUDE]]"
status: stable
ultima_revisao: 2026-05-28
---

# 000 — Índice do Vault FundScope

> Mapa de entrada de todo o conhecimento do projeto. Começa aqui.
> Todos os links nesta página são **relativos** e funcionam tanto no GitHub como no Obsidian.

O FundScope é um sistema de trading automatizado (tríade Clyde / Bonnie / CRO) + dashboard
web em GitHub Pages. Este índice orienta-te pelas três camadas de documentação do repositório.

---

## As três camadas de documentação

| Camada | Onde | O que contém |
|---|---|---|
| **Raiz** | [README.md](README.md), [CLAUDE.md](CLAUDE.md) | Porta de entrada GitHub + diretrizes permanentes do Claude Code |
| **Knowledge layer** | [vault/](vault/) | Notas curadas: MOCs, specs, atoms, templates (teia Obsidian) |
| **Relatórios** | [docs/](docs/) | Runs de backtest, manuais, backlog, prompts |

---

## Hubs principais

- [README.md](README.md) — Map of Content da arquitetura (porta GitHub)
- [CLAUDE.md](CLAUDE.md) — Diretrizes não-negociáveis do bot/executor/frontend
- [MOC — FundScope](vault/mocs/MOC_FundScope.md) — hub central da teia Obsidian

---

## MOCs — Mapas de Conteúdo por domínio

| MOC | Domínio |
|---|---|
| [MOC FundScope](vault/mocs/MOC_FundScope.md) | Raiz do sistema |
| [MOC Clyde](vault/mocs/MOC_Clyde.md) | Motor de execução de ordens |
| [MOC Bonnie](vault/mocs/MOC_Bonnie.md) | Filtro de risco por trade |
| [MOC CRO](vault/mocs/MOC_CRO.md) | Risco sistémico e kill-switch |
| [MOC Frontend](vault/mocs/MOC_Frontend.md) | Dashboard web SPA |
| [MOC Infraestrutura](vault/mocs/MOC_Infraestrutura.md) | Config, dados, VPS, notificações |

---

## Especificações técnicas

| Spec | Conteúdo |
|---|---|
| [CRO_SPEC](vault/specs/CRO_SPEC.md) | Especificação completa do Chief Risk Officer |
| [FUNDSCOPE_CLAUDE_CODE_SPEC](vault/specs/FUNDSCOPE_CLAUDE_CODE_SPEC.md) | Especificação técnica global |
| [ROADMAP_FRONTEND](vault/specs/ROADMAP_FRONTEND.md) | Regras de ouro e roadmap do dashboard |
| [VPS_MIGRATION_SPEC](vault/specs/VPS_MIGRATION_SPEC.md) | Migração para Oracle Cloud VPS |
| [EARNINGS_TAB](vault/specs/EARNINGS_TAB.md) | Separador de resultados financeiros |
| [SPEC_SP500_BACKTEST](vault/specs/SPEC_SP500_BACKTEST.md) | Backtest sobre o universo S&P 500 |
| [SPEC_HANDOFF_SONNET](vault/specs/SPEC_HANDOFF_SONNET.md) | Handoff de contexto entre modelos |
| [FASE-1](vault/specs/FASE-1.md) | Roadmap de evolução e diagnóstico do bot |

---

## Atoms — Notas atómicas (indicadores, APIs, conceitos)

**Indicadores técnicos:** [RSI-14](vault/atoms/atom-rsi14.md) ·
[EMA-50](vault/atoms/atom-ema50.md) · [EMA-200](vault/atoms/atom-ema200.md) ·
[ATR](vault/atoms/atom-atr.md) · [Relative Strength](vault/atoms/atom-rs.md) ·
[Volume Ratio](vault/atoms/atom-volume-ratio.md) ·
[Calmar Ratio](vault/atoms/atom-calmar.md) · [Profit Factor](vault/atoms/atom-profit-factor.md)

**APIs e serviços:** [Trading212](vault/atoms/atom-trading212.md) ·
[Finnhub](vault/atoms/atom-finnhub.md) · [yfinance](vault/atoms/atom-yfinance.md) ·
[Marketaux](vault/atoms/atom-marketaux.md) · [ThreadPoolExecutor](vault/atoms/atom-threadpool.md)

**Prompts:** [Master Prompts](vault/atoms/master_prompts.md)

---

## Runs de backtest / optimização

- [Índice de Runs](docs/runs/README.md) — tabela comparativa e critérios de sucesso
- [run-001](docs/runs/run-001.md) — Baseline v1: Clyde-only
- [run-002](docs/runs/run-002.md) — v1 com 3 bots (Bonnie v1)
- [run-003](docs/runs/run-003.md) — v2: Trailing + Cap 10% + Bonnie v2
- [run-004](docs/runs/run-004.md) — v3: Learner 7 anos + fitness adaptativa
- [run-005](docs/runs/run-005.md) — Kelly Fractional + Bonnie v3
- [run-006](docs/runs/run-006.md) — Bonnie v4 (labels calibradas 4.25×ATR)
- [run-007](docs/run-007.md) — Bonnie v4-clean: correção de data leakage **(referência ativa)**
- [RUN_TEMPLATE](docs/runs/RUN_TEMPLATE.md) — modelo para novas runs

---

## Manuais e planeamento

- [T212 API Manual](docs/T212_API_MANUAL.md) — comportamento real da API Trading 212 (demo)
- [Backlog](docs/backlog.md) — próximas melhorias (desbloqueio 2026-06-24)
- [Prompt — Trading Core](docs/CLAUDE_TRADING_CORE_PROMPT.md) — prompt de revisão do trading core

---

## Meta

- [Relatório de Reestruturação](vault/_meta/REESTRUTURACAO_REPORT.md) — auditoria das 3 fases Obsidian
- [glossary.yml](vault/_meta/glossary.yml) — 81 termos → alvos de wikilink (fonte do `inject_graph_links.py`)
- [Templates](vault/templates/) — `template_atom`, `template_moc`, `template_spec`, `template_learner`

---

## Fora do vault (excluído da teia)

Código Python (`bot/`, `scripts/`, `crawler/`, `ingest/`, `tests/`), dashboards
HTML, JSONs de dados (`data/`, `*.json` na raiz) e artefactos gerados
(`graphify-out/`, `logs/`) **não** são notas do vault — são alvos de link ou runtime.
