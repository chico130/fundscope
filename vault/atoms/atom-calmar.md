---
id: atom-calmar
title: "Calmar Ratio — Retorno Ajustado ao Drawdown"
type: atom
domain: cro
regime: neutral
tags: [atom, metrica, risco, performance, drawdown]
links_obrigatorios:
  parent_moc: "[[MOC_CRO]]"
  vizinhos: "[[cro]] [[reporter]] [[MOC_Bonnie]]"
status: stable
ultima_revisao: 2026-05-19
---

# Calmar Ratio — Retorno Ajustado ao Drawdown

> Métrica de eficiência do sistema: CAGR dividido pelo máximo drawdown.

Retorno: [[MOC_CRO]]

## O que é

O Calmar Ratio = CAGR / Max Drawdown. Um valor > 1.0 significa que o sistema gera mais retorno por unidade de risco de drawdown. O [[MOC_CRO|CRO]] monitoriza o Calmar para detetar degradação da estratégia: se o Calmar cair abaixo de um threshold, pode sinalizar mudança de regime ou deterioração do edge. Complementa o [[atom-profit-factor|Profit Factor]] na avaliação de saúde do sistema.

## Onde vive

| Ficheiro | Papel |
|---|---|
| [[cro]] | Calcula e monitoriza Calmar no `observe()` |
| [[reporter]] | Inclui Calmar no relatório periódico de performance |
| [[data_layer]] | Fornece equity curve para cálculo de drawdown |

## Ligações

- [[MOC_CRO]] — Calmar é uma das métricas de saúde do sistema monitorizadas pelo CRO
- [[atom-profit-factor]] — Profit Factor e Calmar são monitorizados em conjunto
- [[MOC_Bonnie]] — drawdown excessivo pode acionar vetos da Bonnie
- Retorno: [[MOC_CRO]]
