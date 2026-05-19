---
id: atom-ema50
title: "EMA-50 — Média Móvel Exponencial 50"
type: atom
domain: clyde
regime: neutral
tags: [atom, indicador, tendencia, ema]
links_obrigatorios:
  parent_moc: "[[MOC_Clyde]]"
  vizinhos: "[[atom-ema200]] [[strategy]] [[data_layer]]"
status: stable
ultima_revisao: 2026-05-19
---

# EMA-50 — Média Móvel Exponencial 50

> Média de curto-prazo que o [[MOC_Clyde|Clyde]] usa para detetar tendência intermédia.

Retorno: [[MOC_Clyde]]

## O que é

A EMA-50 é uma média móvel exponencial de 50 períodos que dá mais peso aos preços recentes. No FundScope é usada em conjugação com a [[atom-ema200|EMA-200]] para confirmar tendência: preço acima de EMA-50, e EMA-50 acima de EMA-200, sinaliza estrutura bull. Calculada via `compute_ema()` — god node cross-community do [[MOC_Clyde|Clyde]].

## Onde vive

| Ficheiro | Papel |
|---|---|
| [[data_layer]] | Calcula EMA-50 via `compute_ema()` |
| [[strategy]] | Verifica posição do preço face à EMA-50 |
| [[feature_builder]] | Inclui EMA-50 no feature set |

## Ligações

- [[atom-ema200]] — cruzamento EMA-50/EMA-200 (Golden Cross) é sinal estrutural
- [[MOC_Clyde]] — EMA-50 é filtro de tendência no motor VALUE
- [[data_layer]] — `compute_ema()` é o hub central de cálculo de EMAs
- Retorno: [[MOC_Clyde]]
