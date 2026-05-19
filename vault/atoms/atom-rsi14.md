---
id: atom-rsi14
title: "RSI-14 — Índice de Força Relativa"
type: atom
domain: clyde
regime: bull
tags: [atom, indicador, momentum, sinal-A]
links_obrigatorios:
  parent_moc: "[[MOC_Clyde]]"
  vizinhos: "[[strategy]] [[feature_builder]] [[data_layer]]"
status: stable
ultima_revisao: 2026-05-19
---

# RSI-14 — Índice de Força Relativa

> Oscilador de momentum usado pelo [[MOC_Clyde|Clyde]] como sinal A de entrada.

Retorno: [[MOC_Clyde]]

## O que é

O RSI-14 (Relative Strength Index, 14 períodos) mede a velocidade e magnitude das variações de preço numa escala 0–100. Valores acima de 50 indicam momentum comprador dominante. No FundScope, um RSI-14 > 55 é condição necessária (mas não suficiente) para o sinal MOMENTUM do [[MOC_Clyde|Clyde]].

## Onde vive

| Ficheiro | Papel |
|---|---|
| [[feature_builder]] | Calcula RSI-14 a partir de OHLCV |
| [[strategy]] | Aplica threshold (>55) no sinal A |
| [[data_layer]] | Enriquece dados com RSI antes de passar ao bot |

## Ligações

- [[MOC_Clyde]] — RSI-14 é o núcleo do sinal de entrada MOMENTUM
- [[atom-atr]] — ATR complementa o RSI no dimensionamento de stop-loss via [[MOC_Bonnie|Bonnie]]
- [[atom-rs]] — Força Relativa vs. SPY é validada após RSI confirmar momentum
- Retorno: [[MOC_Clyde]]
