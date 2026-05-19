---
id: atom-threadpool
title: "ThreadPoolExecutor — Paralelismo de I/O"
type: atom
domain: infra
regime: n/a
tags: [atom, concorrencia, performance, threads, io]
links_obrigatorios:
  parent_moc: "[[MOC_Infraestrutura]]"
  vizinhos: "[[data_layer]] [[watchlist_manager]] [[atom-yfinance]]"
status: stable
ultima_revisao: 2026-05-19
---

# ThreadPoolExecutor — Paralelismo de I/O

> `concurrent.futures.ThreadPoolExecutor` — acelera downloads de dados paralelos no FundScope.

Retorno: [[MOC_Infraestrutura]]

## O que é

O `ThreadPoolExecutor` do módulo `concurrent.futures` é usado pelo [[data_layer|data_layer.py]] e [[watchlist_manager|watchlist_manager.py]] para descarregar dados de múltiplos tickers em paralelo. Como os downloads são I/O-bound (aguardam resposta de APIs), threads são mais eficientes do que processos. Sem paralelismo, uma watchlist de 50 tickers levaria ~50s; com ThreadPoolExecutor reduz para ~3-5s.

## Onde vive

| Ficheiro | Papel |
|---|---|
| [[data_layer]] | Paraleliza downloads de OHLCV via [[atom-yfinance|yfinance]] |
| [[watchlist_manager]] | Paraleliza verificação de fundamentais via [[atom-finnhub|Finnhub]] |
| [[config]] | Define `MAX_WORKERS` (nº de threads simultâneas) |

## Ligações

- [[MOC_Infraestrutura]] — ThreadPoolExecutor é a camada de performance do data layer
- [[atom-yfinance]] — downloads yfinance correm em threads geridas pelo ThreadPoolExecutor
- [[atom-finnhub]] — chamadas Finnhub também beneficiam do paralelismo
- Retorno: [[MOC_Infraestrutura]]
