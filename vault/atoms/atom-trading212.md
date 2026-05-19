---
id: atom-trading212
title: "Trading212 — Broker e API de Execução"
type: atom
domain: infra
regime: n/a
tags: [atom, broker, api, execucao, ISA]
links_obrigatorios:
  parent_moc: "[[MOC_Infraestrutura]]"
  vizinhos: "[[api_client]] [[execution]] [[config]]"
status: stable
ultima_revisao: 2026-05-19
---

# Trading212 — Broker e API de Execução

> Broker regulado no Reino Unido que serve de ponte entre o FundScope e os mercados reais.

Retorno: [[MOC_Infraestrutura]]

## O que é

O Trading212 é o broker usado pelo FundScope para execução de ordens. Expõe uma API REST não oficial que o [[api_client|api_client.py]] consome. Opera em duas contas separadas: **ALFA** (ISA/Invest real, gerida manualmente) e **BETA** (conta demo para o bot automatizado). As credenciais são guardadas em `.env` e nunca commitadas.

## Onde vive

| Ficheiro | Papel |
|---|---|
| [[api_client]] | Cliente HTTP que consome a API T212 |
| [[execution]] | Submete ordens via api_client |
| [[config]] | Guarda API key e base URL da T212 |

## Ligações

- [[MOC_Infraestrutura]] — T212 é a interface com o mundo real; toda a execução passa aqui
- [[api_client]] — wrappa a API REST do T212 (autenticação, limites de rate)
- [[execution]] — chama api_client para submeter e confirmar ordens
- Retorno: [[MOC_Infraestrutura]]
