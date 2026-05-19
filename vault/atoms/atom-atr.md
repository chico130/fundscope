---
id: atom-atr
title: "ATR — Average True Range"
type: atom
domain: bonnie
regime: neutral
tags: [atom, indicador, volatilidade, stop-loss, dimensionamento]
links_obrigatorios:
  parent_moc: "[[MOC_Bonnie]]"
  vizinhos: "[[bonnie]] [[exit_manager]] [[config]]"
status: stable
ultima_revisao: 2026-05-19
---

# ATR — Average True Range

> Medida de volatilidade que a [[MOC_Bonnie|Bonnie]] usa para dimensionar stop-loss.

Retorno: [[MOC_Bonnie]]

## O que é

O ATR (Average True Range) mede a volatilidade média de uma ação num período (tipicamente 14 dias). No FundScope, o stop-loss inicial é calculado como `preço_entrada - N × ATR` — onde N é um multiplicador definido em [[config|config.py]]. Isto garante que o stop se adapta à volatilidade de cada ação em vez de usar uma percentagem fixa.

## Onde vive

| Ficheiro | Papel |
|---|---|
| [[bonnie]] | Usa ATR para calcular stop-loss inicial |
| [[exit_manager]] | Usa ATR para trailing stop dinâmico |
| [[config]] | Define multiplicador ATR (`atr_stop_multiplier`) |

## Ligações

- [[MOC_Bonnie]] — ATR é o pilar do dimensionamento de risco por posição
- [[atom-rsi14]] — RSI-14 gera o sinal; ATR determina onde sair se o sinal falhar
- [[exit_manager]] — trailing stop baseado em ATR é gerido pelo exit_manager
- Retorno: [[MOC_Bonnie]]
