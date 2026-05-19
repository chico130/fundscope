---
id: atom-finnhub
title: "Finnhub — API de Dados Fundamentais"
type: atom
domain: infra
regime: n/a
tags: [atom, api, dados, fundamentais, earnings]
links_obrigatorios:
  parent_moc: "[[MOC_Infraestrutura]]"
  vizinhos: "[[data_layer]] [[config]] [[atom-yfinance]]"
status: stable
ultima_revisao: 2026-05-19
---

# Finnhub — API de Dados Fundamentais

> Fonte primária de dados fundamentais: P/E, datas de earnings, market cap, e sentimento.

Retorno: [[MOC_Infraestrutura]]

## O que é

A API Finnhub fornece dados fundamentais em tempo real que o [[data_layer|data_layer.py]] agrega no `data.json`. Inclui métricas como P/E ratio, EPS, market cap, datas de apresentação de resultados e notícias de sentimento. A chave de API é lida de `.env` via python-dotenv. Complementa o [[atom-yfinance|yfinance]] (dados técnicos/OHLCV) com a camada fundamental.

## Onde vive

| Ficheiro | Papel |
|---|---|
| [[data_layer]] | Consome a API Finnhub e agrega em data.json |
| [[config]] | Guarda `FINNHUB_API_KEY` de `.env` |
| [[watchlist_manager]] | Usa Finnhub para filtrar por fundamentais |

## Ligações

- [[MOC_Infraestrutura]] — Finnhub é uma das duas fontes de dados externas principais
- [[atom-yfinance]] — yfinance (OHLCV técnicos) + Finnhub (fundamentais) formam o data layer completo
- [[data_layer]] — ponto de integração de todas as fontes de dados externas
- Retorno: [[MOC_Infraestrutura]]
