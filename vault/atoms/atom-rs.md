---
id: atom-rs
title: "RS — Força Relativa vs. SPY"
type: atom
domain: clyde
regime: bull
tags: [atom, indicador, forca-relativa, filtro, SPY]
links_obrigatorios:
  parent_moc: "[[MOC_Clyde]]"
  vizinhos: "[[strategy]] [[data_layer]] [[atom-rsi14]]"
status: stable
ultima_revisao: 2026-05-19
---

# RS — Força Relativa vs. SPY

> Filtro de seleção: o [[MOC_Clyde|Clyde]] só compra ações que batem o S&P 500.

Retorno: [[MOC_Clyde]]

## O que é

A Força Relativa (RS) é o rácio entre o Close de uma ação e o Close do ETF SPY (S&P 500). Uma EMA-20 desse rácio define a tendência de RS: `RS_Bullish = True` se RS > EMA-20 do RS. O motor MOMENTUM do [[MOC_Clyde|Clyde]] só dispara se `RS_Bullish` for True — garante que só se compra ações com desempenho superior ao mercado.

## Onde vive

| Ficheiro | Papel |
|---|---|
| [[data_layer]] | Descarrega SPY em background e calcula RS |
| [[strategy]] | Gate: rejeita MOMENTUM se `RS_Bullish = False` |
| [[feature_builder]] | Inclui RS e EMA-20 do RS no feature set |

## Ligações

- [[MOC_Clyde]] — RS é o segundo filtro do sinal MOMENTUM (após RSI-14)
- [[atom-rsi14]] — RSI-14 confirma momentum interno; RS confirma momentum relativo
- [[atom-volume-ratio]] — ambos são filtros de confirmação antes da entrada
- Retorno: [[MOC_Clyde]]
