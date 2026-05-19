---
id: atom-volume-ratio
title: "Volume Ratio — Rácio de Volume"
type: atom
domain: bonnie
regime: neutral
tags: [atom, indicador, volume, confirmacao, smart-money]
links_obrigatorios:
  parent_moc: "[[MOC_Bonnie]]"
  vizinhos: "[[bonnie]] [[data_layer]] [[feature_builder]]"
status: stable
ultima_revisao: 2026-05-19
---

# Volume Ratio — Rácio de Volume

> Gate de "Smart Money": a [[MOC_Bonnie|Bonnie]] só valida sinais com volume institucional.

Retorno: [[MOC_Bonnie]]

## O que é

O Volume Ratio é calculado como `Volume Diário / Volume_SMA_10` (média móvel de 10 dias). Um rácio > 1.2 indica que o volume do dia superou 20% da média — sinal de participação institucional. A [[MOC_Bonnie|Bonnie]] usa este gate para rejeitar "fakeouts": movimentos de preço sem força de volume real. Rejeições são registadas em `log_decision()` com flag `bonnie_rejected_low_volume`.

## Onde vive

| Ficheiro | Papel |
|---|---|
| [[data_layer]] | Calcula Volume_SMA_10 e Volume_Ratio |
| [[bonnie]] | Gate: rejeita sinal se Volume_Ratio < 1.2 |
| [[feature_builder]] | Inclui Volume_Ratio no feature set |

## Ligações

- [[MOC_Bonnie]] — Volume Ratio é o gate de confirmação institucional da Bonnie
- [[atom-rs]] — ambos são filtros de confirmação de qualidade do sinal
- [[atom-rsi14]] — RSI-14 gera o sinal que o Volume Ratio valida ou rejeita
- Retorno: [[MOC_Bonnie]]
