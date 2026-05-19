---
id: atom-profit-factor
title: "Profit Factor — Rácio Lucro/Perda Bruta"
type: atom
domain: cro
regime: neutral
tags: [atom, metrica, performance, edge, avaliacao]
links_obrigatorios:
  parent_moc: "[[MOC_CRO]]"
  vizinhos: "[[cro]] [[reporter]] [[atom-calmar]]"
status: stable
ultima_revisao: 2026-05-19
---

# Profit Factor — Rácio Lucro/Perda Bruta

> Medida do edge do sistema: Lucro Bruto / Perda Bruta. Acima de 1.5 é considerado robusto.

Retorno: [[MOC_CRO]]

## O que é

O Profit Factor = Lucro Bruto Total / Perda Bruta Total em todos os trades. Um PF > 1.5 indica que o sistema gera 1.5€ de lucro por cada 1€ de perda — edge consistente. Abaixo de 1.0 o sistema destrói capital. O [[MOC_CRO|CRO]] usa o Profit Factor para monitorizar degradação do modelo, especialmente após mudanças de regime ou atualizações do [[MOC_Bonnie|Bonnie]].

## Onde vive

| Ficheiro | Papel |
|---|---|
| [[cro]] | Calcula e alerta se PF cair abaixo de threshold |
| [[reporter]] | Inclui PF no relatório diário/semanal |
| [[evaluate_bonnie]] | Usa PF para avaliar qualidade das decisões da Bonnie |

## Ligações

- [[MOC_CRO]] — PF é KPI primário de saúde do edge do sistema
- [[atom-calmar]] — Calmar e PF são os dois pilares de avaliação de risco/retorno
- [[evaluate_bonnie]] — PF por regime informa o rebalanceamento da Bonnie
- Retorno: [[MOC_CRO]]
