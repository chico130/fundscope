---
id: reestruturacao-report
title: "Relatório de Reestruturação FundScope"
type: spec
domain: geral
regime: n/a
tags: [meta, reestruturacao, auditoria, fase1, fase2, fase3]
links_obrigatorios:
  parent_moc: "[[MOC_FundScope]]"
  vizinhos: "[[MOC_Clyde]] [[MOC_Bonnie]] [[MOC_CRO]]"
status: stable
ultima_revisao: 2026-05-19
---

# Relatório de Reestruturação FundScope

> Auditoria completa das 3 fases de limpeza, taxonomia e construção da teia Obsidian.

Retorno: [[MOC_FundScope]]

---

## Sumário Executivo

| Fase | Commit | Status |
|---|---|---|
| Pré-voo — Graphify | — | ✅ Grafo gerado |
| Fase 1 — Auditoria e Purga | `1030f4a` | ✅ Concluída |
| Fase 2 — Teia Obsidian | `772d73c` | ✅ Concluída |
| Fase 3 — Pipeline de Links | em curso | ✅ Concluída |

---

## Pré-voo — Estado do Grafo

### Antes (com ruído)
- **2580 nós · 3730 arestas · 226 comunidades**
- God Nodes falsos: `f()` (56 edges), `k()` (34 edges) — Templater plugin minificado

### Depois (corpus limpo)
- **2196 nós · 2979 arestas · 196 comunidades**
- 384 nós de ruído eliminados (`.obsidian/`, logs efémeros)
- God Nodes reais:

| Nó | Arestas | Significado |
|---|---|---|
| `fund_data` | 64 | Hub de dados do universo de ações |
| `log_error()` | 53 | Chamado por todos os módulos — [[MOC_Infraestrutura]] |
| `log_decision()` | 31 | Hub de auditoria — [[MOC_Infraestrutura]] |
| `compute_ema()` | — | Ponte cross-community (betweenness 0.063) — [[MOC_Clyde]] |

**Redução de tokens por query: 83x** (239K tokens corpus → ~2.8K tokens por query).

---

## Fase 1 — Auditoria e Purga de Ficheiros

### Movimentos Executados

| Ficheiro | De | Para | Ação |
|---|---|---|---|
| `t212_debug.py` | raiz | `tests/` | `git mv` |
| `test_connection.py` | raiz | `tests/` | `git mv` |
| `test_ordem_demo.py` | raiz | `tests/` | `git mv` |
| `test_telegram.py` | raiz | `tests/` | `git mv` |
| `find_trades.py` | raiz | `tests/` | `git mv` |
| `backtest_comparison.py` | raiz | `archive/` | `git mv` |
| `stress_test_2023_2024.py` | raiz | `archive/` | `git mv` |
| `Ligar_FundScope_VPS.bat` | raiz | `scripts/` | `git mv` |
| `validate_pipeline.py` | raiz | `scripts/` | `mv` |
| `CRO_SPEC.md` | raiz | `vault/specs/` | `git mv` |
| `FASE-1.md` | raiz | `vault/specs/` | `git mv` |
| `FUNDSCOPE_CLAUDE_CODE_SPEC.md` | raiz | `vault/specs/` | `git mv` |
| `SPEC_HANDOFF_SONNET.md` | raiz | `vault/specs/` | `git mv` |
| `VPS_MIGRATION_SPEC.md` | raiz | `vault/specs/` | `git mv` |
| `ROADMAP_FRONTEND.md` | raiz | `vault/specs/` | `git mv` |
| `EARNINGS_TAB.md` | raiz | `vault/specs/` | `git mv` |
| `Master Prompts.md` | raiz | `vault/atoms/` | `git mv` |
| `TEMPLATE_LEARNER.md` | raiz | `vault/templates/` | `git mv` |
| `data/beta/beta_trades.backup.json` | data/beta | — | `git rm` |

### O que ficou na raiz (intencionalmente)

| Ficheiro | Razão |
|---|---|
| `*.html` | GitHub Pages serve da raiz |
| `data.json`, `markets.json`, `portfolio.json`, etc. | GitHub Actions escrevem nestes paths |
| `serve.py`, `update_*.py` | Referenciados nos workflows .yml |
| `README.md`, `CLAUDE.md` | Navegação e instruções globais |

### .graphifyignore criado

Exclui do corpus: `.obsidian/`, `graphify-out/`, `logs/trades/`, `logs/errors/`, `__pycache__/`, `*.lock`.

---

## Fase 2 — Engenharia da Teia Obsidian

### MOCs Criados (6 super-nós)

| MOC | Links no corpo | Domínio |
|---|---|---|
| [[MOC_FundScope]] | 20+ | Raiz do sistema |
| [[MOC_Clyde]] | 15+ | Motor de execução |
| [[MOC_Bonnie]] | 15+ | Filtro de risco |
| [[MOC_CRO]] | 15+ | Risco sistémico |
| [[MOC_Frontend]] | 12+ | Dashboard web |
| [[MOC_Infraestrutura]] | 18+ | Config, data, VPS |

### Frontmatter YAML injetado em 9 ficheiros

Campos: `id`, `title`, `type`, `domain`, `regime`, `tags`, `links_obrigatorios`, `status`, `ultima_revisao`.

Script: `scripts/inject_frontmatter.py` (idempotente).

### Templates criados (3)

- `vault/templates/template_atom.md`
- `vault/templates/template_moc.md`
- `vault/templates/template_spec.md`

### Glossário criado

`vault/_meta/glossary.yml` — 81 termos canónicos mapeados para MOCs/atoms.

---

## Fase 3 — Pipeline de Links (Token-Saver)

### Injeção de Links `[[...]]`

| Ronda | Links injetados | Ficheiros |
|---|---|---|
| 1ª (first_only) | 126 | 15 |
| 2ª (first_only) | 52 | 14 |
| 3ª (all-occurrences) | 89 | 11 |
| **Total** | **267** | **18** |

Script: `scripts/inject_graph_links.py` — idempotente confirmado (0 links novos na 4ª passagem).

### Resultado do Linter (graph_lint.py)

```
0 ERROR(s) — vault estruturalmente válido
150 WARN(s) — classificados abaixo
```

| Tipo de WARN | Causa | Ação necessária |
|---|---|---|
| R-CN6 links a `.py` | Obsidian resolve por ficheiro fora do vault | ✅ Nenhuma — comportamento esperado |
| R-CN6 links a `atom-*` | Notas atómicas ainda não criadas | 🔜 Fase 4 — criar atoms |
| R-CN6 links a slugs curtos | Targets do glossário sem nota correspondente | 🔜 Fase 4 — criar atoms |
| R-CN3 sem link de retorno | 5 specs sem `[[MOC_pai]]` no corpo | 🔜 Corrigir manualmente |
| R-CN6 em templates | Placeholders de template | ✅ Nenhuma — esperado |

### Ficheiros com R-CN3 pendente (5)

- `vault/atoms/master_prompts.md` → adicionar `[[MOC_FundScope]]`
- `vault/specs/EARNINGS_TAB.md` → adicionar `[[MOC_Frontend]]`
- `vault/specs/FASE-1.md` → adicionar `[[MOC_FundScope]]`
- `vault/specs/FUNDSCOPE_CLAUDE_CODE_SPEC.md` → adicionar `[[MOC_FundScope]]`
- `vault/specs/SPEC_HANDOFF_SONNET.md` → adicionar `[[MOC_Infraestrutura]]`

---

## Estrutura de Pastas (estado final)

```
Fundscope/
├── bot/               ← Código ativo do bot (26 módulos Python)
├── data/              ← Dados de runtime (beta/, user_credentials)
├── logs/              ← Logs efémeros (gitignored parcialmente)
├── tests/             ← Scripts de teste (5 ficheiros)
├── archive/           ← Scripts legacy (2 ficheiros)
├── scripts/           ← Utilitários CLI (inject_frontmatter, inject_graph_links, graph_lint)
├── vault/
│   ├── mocs/          ← 6 super-nós MOC
│   ├── specs/         ← 7 especificações técnicas
│   ├── atoms/         ← 1 nota atómica (expandir na Fase 4)
│   ├── templates/     ← 4 templates canónicos
│   └── _meta/         ← glossary.yml, REESTRUTURACAO_REPORT.md
├── graphify-out/      ← Grafo de conhecimento (gitignored)
├── .graphifyignore    ← Exclusões do corpus graphify
├── README.md          ← MOC raiz simplificado
├── CLAUDE.md          ← Instruções globais do Claude Code
└── [HTML + JSONs]     ← GitHub Pages (raiz, imutável)
```

---

## Próximos Passos (Fase 4 — Densificação)

1. **Criar notas atómicas** para os `atom-*` do glossário:
   - `vault/atoms/atom_rsi14.md`, `atom_ema.md`, `atom_atr.md`, `atom_trading212.md`, etc.
2. **Corrigir R-CN3** nos 5 ficheiros listados acima
3. **Dissecar `compute_ema()`** — rastrear o "fio de Ariadne" no grafo limpo
4. **Migrar HTML/JSON da raiz** para `web/` — requer configurar GitHub Pages para servir de `web/`
5. **Executar `/graphify --update`** após movimentos da Fase 4 para atualizar o grafo
