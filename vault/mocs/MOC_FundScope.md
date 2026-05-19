---
id: moc-fundscope
title: "MOC — FundScope (Raiz)"
type: moc
domain: geral
regime: n/a
tags: [moc, root, fundscope]
links_obrigatorios:
  parent_moc: "self"
  vizinhos: "[[MOC_Clyde]] [[MOC_Bonnie]] [[MOC_CRO]] [[MOC_Frontend]] [[MOC_Infraestrutura]]"
status: stable
ultima_revisao: 2026-05-19
---

# MOC — FundScope

> Hub de navegação central. Todos os caminhos do conhecimento passam aqui.

---

## Visão Geral

O FundScope é um sistema de trading automatizado + dashboard web (GitHub Pages).
Opera em duas camadas paralelas:

- **ALFA** — Conta real ISA/Invest (Trading212 live), gerida manualmente.
- **BETA** — Bot automatizado em paper trading (T212 demo), controlado pela tríade [[MOC_Clyde]] / [[MOC_Bonnie]] / [[MOC_CRO]].

---

## Domínios — 5 Super-Nós

| MOC | Domínio | Ficheiros-chave |
|---|---|---|
| [[MOC_Clyde]] | Motor de execução de ordens | strategy.py, execution.py, exit_manager.py |
| [[MOC_Bonnie]] | Filtro de risco por trade | bonnie.py, learner.py, evaluate_bonnie.py |
| [[MOC_CRO]] | Risco sistémico e kill-switch | cro.py, regime_detector.py, [[CRO_SPEC]] |
| [[MOC_Frontend]] | Dashboard web SPA | portfolio.html, markets.html, serve.py |
| [[MOC_Infraestrutura]] | Config, dados, VPS, notificações | config.py, data_layer.py, notifier.py |

---

## Arquitetura do Bot (fluxo)

```
   MERCADO
      │
 [[regime_detector.py]]     ← Classifica regime macro (bull/bear)
      │
 [[cro.py]] — CRO           ← Aprova estado do sistema → [[MOC_CRO]]
    ╱          ╲
[[bonnie.py]]  [[strategy.py]]  ← Risco / Sinais → [[MOC_Bonnie]] / [[MOC_Clyde]]
    ╲          ╱
  [[execution.py]]          ← Submete ordens T212
      │
  [[api_client.py]]         ← Cliente HTTP T212 API
```

---

## Pipeline Principal

| Ficheiro | Fase |
|---|---|
| [[phase0.py]] | Observação — lê dados, calcula técnicos, sem ordens |
| [[main.py]] | Orquestrador do ciclo live |
| [[watchdog.py]] | Monitor de saúde do processo |
| [[watchlist_manager.py]] | Gestão dinâmica da watchlist |

---

## Camada de Dados (JSON — contratos entre bot e frontend)

| JSON | Produtor | Consumidor |
|---|---|---|
| data/beta/beta_summary.json | bot | [[MOC_Frontend]] |
| data/beta/beta_positions.json | bot | [[MOC_Frontend]] |
| data/beta/beta_equity.json | bot | [[MOC_Frontend]] |
| data/beta/beta_trades.json | bot | [[MOC_Frontend]], [[MOC_CRO]] |
| data/beta/cro_insights.json | cro.py | [[MOC_Frontend]] |
| data.json | update_prices.py | [[MOC_Frontend]] |

---

## Documentação

| Ficheiro | Conteúdo |
|---|---|
| [[CRO_SPEC]] | Especificação completa do Chief Risk Officer |
| [[FASE-1]] | Roadmap de evolução e diagnóstico do bot |
| [[FUNDSCOPE_CLAUDE_CODE_SPEC]] | Especificação técnica global |
| [[ROADMAP_FRONTEND]] | Regras de ouro e roadmap do dashboard |
| [[VPS_MIGRATION_SPEC]] | Migração para Oracle Cloud VPS |

---

## Comandos Rápidos

```bash
python bot/phase0.py           # Execução manual do pipeline
python -m bot.mass_backtest    # Análise estatística em massa
/graphify .                    # Actualizar grafo de conhecimento
python scripts/validate_pipeline.py  # Validar pipeline
```

---

## God Nodes do Sistema (grafo limpo)

1. `fund_data` — 64 ligações (hub de dados do universo de ações)
2. `log_error()` — 53 ligações ([[MOC_Infraestrutura]])
3. `log_decision()` — 31 ligações ([[MOC_Infraestrutura]])
4. `compute_ema()` — ponte cross-community (Clyde ↔ Backtest ↔ Risco ↔ Data Layer)
