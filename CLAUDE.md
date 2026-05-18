# Diretrizes do FundScope

## Regra de Ouro de Infraestrutura (Poupança de Tokens)
1. Antes de responderes a qualquer questão sobre a arquitetura do robô, o fluxo entre Clyde/Bonnie/CRO, ou dependências de ficheiros, deves consultar estritamente o `GRAPH_REPORT.md` gerado pelo Graphify.
2. NÃO leias os ficheiros de código completos (como `strategy.py`, `cro.py` ou `bonnie.py`) a menos que o utilizador te peça para alterar linhas de código específicas desses ficheiros. Confia na estrutura do grafo para entenderes as dependências.

## Comandos Úteis do Projeto
- **Análise Estatística/Backtest:** `python -m bot.mass_backtest`
- **Execução Manual do Pipeline:** `python bot/phase0.py`
- **Atualização do Grafo de Conhecimento:** `/graphify .`
- **Validação de Sintaxe:** `python -c "import ast; ast.parse(open('bot/bonnie.py').read())"`