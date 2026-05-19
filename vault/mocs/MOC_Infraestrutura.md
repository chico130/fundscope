---
id: moc-infraestrutura
title: "MOC — Infraestrutura (Config, Dados, VPS, Notificações)"
type: moc
domain: infra
regime: n/a
tags: [moc, infra, config, vps, logging, telegram, data-layer]
links_obrigatorios:
  parent_moc: "[[MOC_FundScope]]"
  vizinhos: "[[MOC_CRO]] [[MOC_Clyde]] [[MOC_Frontend]]"
status: stable
ultima_revisao: 2026-05-19
---

# MOC — Infraestrutura

> Camada de suporte que alimenta todas as outras: configuração, acesso a dados, logging, notificações e orquestração VPS.

Hub: [[MOC_FundScope]] → este MOC → módulos de suporte.

---

## Módulos Principais

| Ficheiro | Responsabilidade |
|---|---|
| [[config.py]] | Configuração central: parâmetros de risco, API keys, paths |
| [[data_layer.py]] | Acesso e enriquecimento técnico dos dados de portfolio |
| [[notifier.py]] | Notificações [[MOC_Infraestrutura|Telegram]] (alertas, relatórios diários, insights [[MOC_CRO|CRO]]) |
| [[reporter.py]] | Geração de relatórios periódicos de performance |
| [[logger.py]] | Logging de decisões e erros (god nodes: [[MOC_Infraestrutura|log_error]], [[MOC_Infraestrutura|log_decision]]) |
| [[watchdog.py]] | Monitor de saúde do processo do bot |
| [[watchlist_manager.py]] | Gestão dinâmica da watchlist de tickers |

---

## God Nodes de Infraestrutura

Os dois nós mais conectados do sistema vivem aqui:
- **`log_error()`** — 53 arestas — chamado por todos os módulos do bot
- **`log_decision()`** — 31 arestas — hub central de auditoria de decisões

Qualquer módulo que falhe ou decida, passa por [[logger.py]].

---

## Configuração Central ([[config|config.py]])

[[config|config.py]] é a fonte de verdade para:
- Parâmetros de risco (RSI thresholds, EMA periods, [[atom-atr|ATR]] multipliers)
- API keys (lidas de .env via python-dotenv)
- Paths de ficheiros de dados
- Flags de feature (BETA mode, live mode)

---

## Data Layer ([[data_layer|data_layer.py]])

Agrega dados de múltiplas fontes:
- `data.json` (preços, fundamentals via [[atom-finnhub|Finnhub]] + [[atom-yfinance|yfinance]])
- `data/beta/beta_positions.json` (posições abertas)
- `data/beta/beta_equity.json` (curva de equity)
- Calcula indicadores técnicos adicionais em tempo real

---

## Logging ([[logger|logger.py]])

| Função | Destino |
|---|---|
| `log_error()` | logs/errors/YYYY-MM-DD.json |
| `log_decision()` | logs/trades/YYYY-MM-DD.json |
| `log_bonnie()` | logs/bonnie_log.json |

Os logs em `logs/trades/` e `logs/errors/` são excluídos do grafo via `.graphifyignore` (efémeros).

---

## Notificações [[MOC_Infraestrutura|Telegram]] ([[notifier|notifier.py]])

- Alertas de trade executado / vetado
- Relatório diário pós-fecho de mercado
- Insights cognitivos do [[MOC_CRO]] (via [[MOC_CRO|cro_insights.json]])
- Kill-switch activado

---

## Orquestração VPS ([[VPS_MIGRATION_SPEC|Oracle Cloud]])

Spec completa: [[VPS_MIGRATION_SPEC]]

| Componente | Papel |
|---|---|
| [[VPS_MIGRATION_SPEC|systemd]] timers | Substituem [[MOC_Frontend|GitHub Actions]] para execução local |
| [[VPS_MIGRATION_SPEC|Caddy]] proxy | HTTPS + reverse proxy para [[serve|serve.py]] |
| UFW + fail2ban | Segurança de rede |
| fs-run wrapper | Isolamento de processos do bot |
| Heartbeat | Monitor de saúde externo |

---

## Ligações Cruzadas

- [[MOC_CRO]] — [[notifier|notifier.py]] envia insights do [[MOC_CRO|CRO]]; [[logger|logger.py]] regista decisões do [[MOC_CRO|CRO]]
- [[MOC_Clyde]] — [[config|config.py]] define parâmetros de todos os sinais do [[MOC_Clyde|Clyde]]
- [[MOC_Bonnie]] — [[MOC_Infraestrutura|config_risco.json]] é gerido pela infra; lido pela [[MOC_Bonnie|Bonnie]]
- [[MOC_Frontend]] — [[serve|serve.py]] é infraestrutura de desenvolvimento local do frontend
- [[VPS_MIGRATION_SPEC]] — plano de migração para VPS dedicado
