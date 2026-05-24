# Diretrizes do FundScope

## Regra de Ouro de Infraestrutura (PoupanÃ§a de Tokens)
1. Antes de responderes a qualquer questÃ£o sobre a arquitetura do robÃ´, o fluxo entre Clyde/Bonnie/CRO, ou dependÃªncias de ficheiros, deves consultar estritamente o [[GRAPH_REPORT.md]] gerado pelo Graphify.
2. NÃƒO leias os ficheiros de cÃ³digo completos (como [[strategy.py]], [[cro.py]] ou [[bonnie.py]]) a menos que o utilizador te peÃ§a para alterar linhas de cÃ³digo especÃ­ficas desses ficheiros. Confia na estrutura do grafo para entenderes as dependÃªncias.

## Comandos Ãšteis do Projeto
- **AnÃ¡lise EstatÃ­stica/Backtest:** `python -m bot.mass_backtest`
- **ExecuÃ§Ã£o Manual do Pipeline:** `python bot/phase0.py`
- **AtualizaÃ§Ã£o do Grafo de Conhecimento:** `/graphify .`
- **ValidaÃ§Ã£o de Sintaxe:** `python -c "import ast; ast.parse(open('bot/bonnie.py').read())"`
---
## Auto-Sync: 2026-05-24 02:11
- PC: DESKTOP-NGIATI2
- Ultimo commit: d205750 - feat(backtest+learner): v2 evoluído — trailing, cap 10%, Bonnie v2, Learner + docs runs
- Learner: verificar data/beta/ para runs recentes
---