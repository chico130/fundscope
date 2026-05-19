---
id: atom-ema200
title: "EMA-200 — Média Móvel Exponencial 200"
type: atom
domain: clyde
regime: neutral
tags: [atom, indicador, tendencia, ema, long-term]
links_obrigatorios:
  parent_moc: "[[MOC_Clyde]]"
  vizinhos: "[[atom-ema50]] [[strategy]] [[MOC_CRO]]"
status: stable
ultima_revisao: 2026-05-19
---

# EMA-200 — Média Móvel Exponencial 200

> Barômetro de tendência de longo prazo — linha de defesa estrutural do [[MOC_Clyde|Clyde]] e do [[MOC_CRO|CRO]].

Retorno: [[MOC_Clyde]]

## O que é

A EMA-200 define o regime de mercado de longo prazo. Preço acima de EMA-200 = bull estrutural; abaixo = bear. O [[MOC_CRO|CRO]] usa a EMA-200 do SPY como input no `regime_detector` para classificar o macro-regime. O [[MOC_Clyde|Clyde]] usa a EMA-200 individual de cada ação como filtro de seleção. Calculada por `compute_ema()`.

## Onde vive

| Ficheiro | Papel |
|---|---|
| [[data_layer]] | Calcula EMA-200 via `compute_ema()` |
| [[regime_detector]] | Usa EMA-200 do SPY para classificar bull/bear |
| [[strategy]] | Filtra ações acima/abaixo da EMA-200 |

## Ligações

- [[atom-ema50]] — cruzamento EMA-50/EMA-200 é sinal de Golden/Death Cross
- [[MOC_CRO]] — EMA-200 do SPY alimenta o `regime_detector`
- [[MOC_Clyde]] — filtro estrutural em todas as estratégias do Clyde
- Retorno: [[MOC_Clyde]]
