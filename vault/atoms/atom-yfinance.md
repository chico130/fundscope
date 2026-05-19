---
id: atom-yfinance
title: "yfinance — Dados de Preço e Técnicos"
type: atom
domain: infra
regime: n/a
tags: [atom, api, dados, precos, OHLCV, yahoo]
links_obrigatorios:
  parent_moc: "[[MOC_Infraestrutura]]"
  vizinhos: "[[data_layer]] [[atom-finnhub]] [[config]]"
status: stable
ultima_revisao: 2026-05-19
---

# yfinance — Dados de Preço e Técnicos

> Biblioteca Python que descarrega dados OHLCV do Yahoo Finance — coluna vertebral técnica do FundScope.

Retorno: [[MOC_Infraestrutura]]

## O que é

O `yfinance` é a biblioteca Python principal para descarregar dados históricos e em tempo real de preços (Open, High, Low, Close, Volume). O [[data_layer|data_layer.py]] usa-o para popular `data.json` com séries temporais de todos os tickers da watchlist, incluindo o SPY como benchmark. É gratuito e sem autenticação, mas sujeito a rate limits. Complementado pelo [[atom-finnhub|Finnhub]] para dados fundamentais.

## Onde vive

| Ficheiro | Papel |
|---|---|
| [[data_layer]] | Chama `yf.download()` para obter OHLCV |
| [[feature_builder]] | Usa os dados yfinance para calcular indicadores |
| [[atom-threadpool]] | Downloads yfinance correm em threads paralelas |

## Ligações

- [[MOC_Infraestrutura]] — yfinance é a fonte primária de dados de preço do sistema
- [[atom-finnhub]] — yfinance (OHLCV) + Finnhub (fundamentais) = data layer completo
- [[atom-threadpool]] — ThreadPoolExecutor acelera downloads yfinance em paralelo
- Retorno: [[MOC_Infraestrutura]]
