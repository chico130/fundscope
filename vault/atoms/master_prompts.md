---
id: atom-master-prompts
title: "Master Prompts — Gates do Sistema"
type: atom
domain: geral
regime: n/a
tags: [atom, prompts, clyde, bonnie, gates]
links_obrigatorios:
  parent_moc: "[[MOC_FundScope]]"
  vizinhos: "[[MOC_Clyde]] [[MOC_Bonnie]] [[MOC_CRO]]"
status: stable
ultima_revisao: 2026-05-19
---
# 🏛️ Biblioteca de Master Prompts — FundScope

Nesta nota estão guardadas as instruções estruturais de elite para os upgrades do sistema. Sempre que for necessário reconstruir ou recalibrar os motores, usar estes prompts no Claude Code.

---

## 🏎️ 1. Módulo do Clyde (Estratégia, RS Gate & Earnings)
**Objetivo:** Elevar o Clyde a 10/10 com Força Relativa face ao SPY e bloqueio de Earnings.

```text
Claude, o nosso objetivo agora é elevar o motor estratégico (Clyde) a 10/10. Vamos dar-lhe contexto macro e noção de calendário. 

Antes de começares, consulta o `GRAPH_REPORT.md` para garantires que manténs as ligações perfeitas com a Bonnie e o CRO. Preciso que implementes as seguintes alterações passo a passo:

1. Integração do Benchmark (data_layer): 
No ficheiro responsável por descarregar e processar os dados de mercado, adiciona o download em background do ETF 'SPY' (S&P 500). Cria uma métrica de Força Relativa (Relative Strength = Close da Ação / Close do SPY) e calcula a EMA-20 desse rácio. Adiciona uma coluna booleana `RS_Bullish` (True se RS > EMA-20 do RS).

2. Filtro de Força Relativa (strategy.py):
Na função que gera os sinais do Clyde, impõe uma nova regra: O motor de `MOMENTUM` só pode disparar uma ordem de compra se o `RS_Bullish` for True. Se a ação não estiver a esmagar o S&P 500, o Clyde não a quer. (O motor VALUE ignora esta regra).

3. Radar de Earnings (strategy.py):
Faz o Clyde ler o ficheiro `data/beta/earnings_ai.json`. Se a ação tiver a data de apresentação de resultados (Earnings) marcada para os próximos 3 dias úteis, o Clyde deve bloquear imediatamente qualquer sinal de `MOMENTUM`. 

4. Telemetria:
Garante que se uma ação for rejeitada por "Fraqueza Relativa face ao SPY" ou por "Earnings Iminentes", isso é registado de forma clara nas funções de `log_decision()` para eu conseguir ver isso no painel.

Revê o teu plano estrutural antes de escreveres o código e avisa-me quando os ficheiros estiverem atualizados.
```

---

## 🛡️ 2. Módulo da Bonnie (Smart Money Gate & Volume Profile)
**Objetivo:** Elevar a Bonnie a 10/10 como auditora de confirmação institucional com filtro de volume.

```text
Claude, o Clyde já está no nível 10/10 com o filtro de Força Relativa e o Radar de Earnings. O nosso objetivo agora é elevar a Bonnie (bonnie.py) a 10/10. Ela é a nossa auditora de confirmação institucional.

Antes de começares, consulta o `GRAPH_REPORT.md` e a estrutura do `data_layer.py` para garantires o fluxo correto de dados. Preciso que implementes as seguintes alterações de forma cirúrgica na lógica da Bonnie:

1. Expansão de Dados (data_layer):
No `data_layer.py` (ou onde geras os technicals), calcula o "Volume Profile" básico: além do volume diário, calcula a média móvel de volume dos últimos 10 dias (Volume_SMA_10) e uma coluna de rácio de volume (Volume_Ratio = Volume Diário / Volume_SMA_10).

2. Filtro de "Smart Money" (bonnie.py):
Na função principal onde a Bonnie valida os sinais do Clyde, implementa o seguinte gate de segurança para TODOS os sinais (quer Value, quer Momentum):
- A Bonnie SÓ PODE validar a compra se o Volume_Ratio do dia do sinal (ou do dia anterior, se o sinal for detetado em after-market) for MAIOR que 1.2 (ou seja, o volume transacionado foi pelo menos 20% superior à média das últimas duas semanas).
- Se o Volume_Ratio for menor que 1.2, significa que é um "Fakeout" (movimento sem força institucional) e a Bonnie tem de rejeitar o sinal.

3. Telemetria e Logs:
Se a Bonnie rejeitar um sinal do Clyde devido à falta de volume, garante que isso é registado no `log_decision()` com a flag `bonnie_rejected_low_volume`.

NÃO alteres as regras de Value ou Momentum do Clyde, foca-te apenas no gate de volume da Bonnie. Avisa-me quando o código estiver pronto para testar.
```
