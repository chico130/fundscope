---
id: moc-<dominio>
title: "MOC — <Domínio>"
type: moc
domain: <clyde|bonnie|cro|frontend|infra|data|geral>
regime: n/a
tags: [moc, <dominio>]
links_obrigatorios:
  parent_moc: "[[MOC_FundScope]]"
  vizinhos: "[[MOC_<dominio2>]] [[MOC_<dominio3>]]"
status: stable
ultima_revisao: <YYYY-MM-DD>
---

# MOC — <Domínio>

> Super-nó de navegação. Liga todos os conceitos do domínio <domínio>.

Hub: [[MOC_FundScope]] → este MOC → notas atómicas abaixo.

---

## Módulos Principais

| Ficheiro / Nota | Responsabilidade |
|---|---|
| [[ficheiro.py]] | <descrição> |
| [[atom-conceito]] | <descrição> |

---

## Especificações

- [[vault/specs/SPEC.md]] — <título>

---

## Fluxo de Dados

```
<entrada> → [[modulo_A]] → [[modulo_B]] → <saída>
```

---

## Ligações Cruzadas (inter-MOC)

- [[MOC_<dominio2>]] — <porquê ligado>
- [[MOC_<dominio3>]] — <porquê ligado>
