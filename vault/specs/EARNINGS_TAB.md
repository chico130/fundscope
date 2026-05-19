---
id: spec-earnings
title: "Earnings Tab — Especificação"
type: spec
domain: frontend
regime: n/a
tags: [spec, earnings, frontend, calendario]
links_obrigatorios:
  parent_moc: "[[MOC_Frontend]]"
  vizinhos: "[[MOC_Clyde]] [[MOC_Bonnie]]"
status: stable
ultima_revisao: 2026-05-19
---
# Tab de Earnings — Especificação

## Objetivo
Criar uma nova página `earnings.html` no FundScope que mostre os earnings relevantes das próximas 2 semanas, com destaque para empresas na watchlist e no portfólio.

---

## Prompt para o Claude Code

Cria uma nova página `earnings.html` no projeto FundScope com os seguintes requisitos:

### Dados — `update_earnings.py` + `earnings.json`

- Lista de empresas com earnings nos próximos 14 dias, gerada via yfinance (`ticker.calendar`)
- Campos por empresa:
  - `ticker` — símbolo da empresa
  - `nome` — nome completo
  - `data` — data do earnings (YYYY-MM-DD)
  - `hora` — "BMO" (Before Market Open) ou "AMC" (After Market Close)
  - `eps_estimado` — EPS consenso dos analistas
  - `eps_anterior` — EPS da época anterior
  - `revenue_estimado` — Revenue estimado
  - `revenue_anterior` — Revenue da época anterior
  - `surpresa_media_pct` — média das surpresas das últimas 4 épocas em %
- O script deve fazer `git add earnings.json && git commit && git push` automaticamente após gerar o ficheiro

### Interface — `earnings.html`

- Tabela ordenada por data (mais próximos primeiro)
- Filtros: "Esta semana" / "Próxima semana" / "Todos"
- Destacar em **amarelo** empresas presentes na watchlist (`data.json`)
- Destacar em **vermelho** empresas com posição aberta no portfólio (`portfolio.json`)
- Badge **BMO** (verde) ou **AMC** (laranja) para indicar hora do earnings
- Coluna de surpresa histórica com ícone ✅ se positiva ou ❌ se negativa
- Design consistente com `markets.html`, `news.html` e restantes páginas
- Adicionar link "Earnings" na navbar de **todas** as páginas existentes

### Task Scheduler

- Adicionar `update_earnings.py` ao `Setup_Tasks_Admin.bat` para correr **1x/dia às 07:00**
- Comando: `py update_earnings.py`

### Regra Bonnie/Clyde

- Se uma empresa tiver earnings em menos de 2 dias e o Clyde tiver posição aberta, a Bonnie deve registar um alerta no `bonnie_log.json` com tipo `"earnings_risk"`
- Esta regra usa o parâmetro `no_trade_before_earnings_days: 2` já existente em `config.py`

---

## Watchlist de empresas a monitorizar (sugestão inicial)

```
AAPL, MSFT, GOOGL, AMZN, META, NVDA, TSLA, AMD, NFLX, CRM,
ASML, SAP, UBER, SHOP, COIN, PLTR, SQ, PYPL, SNOW, NET
```

---

## Notas

- yfinance `ticker.calendar` devolve o próximo earnings date e estimativas
- Para surpresas históricas usar `ticker.earnings_history`
- Fallback: se yfinance não tiver dados para um ticker, omitir silenciosamente
- O `earnings.json` deve ter o campo `last_updated` com timestamp ISO 8601
