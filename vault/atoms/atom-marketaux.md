---
id: atom-marketaux
title: "Marketaux — API de Notícias e Sentimento"
type: atom
domain: infra
regime: n/a
tags: [atom, api, noticias, sentimento, NLP]
links_obrigatorios:
  parent_moc: "[[MOC_Infraestrutura]]"
  vizinhos: "[[data_layer]] [[atom-finnhub]] [[MOC_CRO]]"
status: stable
ultima_revisao: 2026-05-19
---

# Marketaux — API de Notícias e Sentimento

> Fonte de notícias financeiras com scores de sentimento pré-processados por NLP.

Retorno: [[MOC_Infraestrutura]]

## O que é

A API Marketaux fornece notícias financeiras em tempo real com scores de sentimento (positivo/negativo/neutro) calculados por modelos NLP. O [[data_layer|data_layer.py]] usa-a para enriquecer o contexto macro e de cada ticker. O [[MOC_CRO|CRO]] pode consumir estes scores de sentimento como input adicional para o `regime_detector` — sentimento negativo extremo pode reforçar sinais de bear.

## Onde vive

| Ficheiro | Papel |
|---|---|
| [[data_layer]] | Consome a API Marketaux e agrega scores |
| [[config]] | Guarda `MARKETAUX_API_KEY` de `.env` |
| [[regime_detector]] | Usa sentimento Marketaux como input macro |

## Ligações

- [[MOC_Infraestrutura]] — Marketaux é a terceira fonte de dados externos do sistema
- [[atom-finnhub]] — Finnhub (fundamentais estruturais) + Marketaux (sentimento) = contexto completo
- [[MOC_CRO]] — sentimento agregado alimenta o `regime_detector`
- Retorno: [[MOC_Infraestrutura]]
