---
id: spec-fase1
title: "FASE-1 — Roadmap e Diagnóstico do Bot"
type: spec
domain: geral
regime: n/a
tags: [spec, roadmap, bot, diagnostico]
links_obrigatorios:
  parent_moc: "[[MOC_FundScope]]"
  vizinhos: "[[MOC_Clyde]] [[MOC_Bonnie]] [[MOC_CRO]]"
status: stable
ultima_revisao: 2026-05-19
---
# FundScope Bot — Relatório de Diagnóstico, Estratégia e Roadmap de Evolução

Retorno: [[MOC_FundScope]]

> **Destinatário:** Claude Code (configuração e desenvolvimento do bot)
> **Fonte:** Análise do repositório `chico130/fundscope` + sessão de estratégia com o proprietário
> **Data:** Maio 2026
> **Objetivo:** Explicar a dinâmica atual do bot, compilar os insights da sessão de estratégia, e definir um plano de evolução faseado para passar de "modo observação" a "modo autónomo com aprovação humana".

---

## 1. Arquitetura Atual do Bot

### 1.1 Estrutura de Ficheiros

O bot reside na pasta `/bot` e é composto pelos seguintes módulos:

| Ficheiro | Função |
|---|---|
| `main.py` | Ponto de entrada; orquestra o ciclo de execução |
| `phase0.py` | Modo observação — lê dados, calcula técnicos, sugere ações, **não executa ordens** |
| `strategy.py` | Geração de sinais (RSI, EMA, volume) e propostas de trade |
| `learner.py` | Análise de trades fechados, deteção de padrões de erro, sugestões de parâmetros |
| `execution.py` | Submissão de ordens à API da [[atom-trading212|Trading212]] |
| `data_layer.py` | Acesso a dados de portfolio e enriquecimento técnico |
| `api_client.py` | Cliente HTTP para a API da [[atom-trading212|Trading212]] (demo e live) |
| `reporter.py` | Geração de relatórios periódicos |
| `logger.py` | Logging de decisões e erros |
| `config.py` | Configuração central: parâmetros de risco, API keys, paths |

### 1.2 Dinâmica de Funcionamento (Fase 0 — Estado Atual)

O bot está atualmente em **Fase 0 (modo só-leitura)**. O ciclo de execução é:

```
1. Ligar o bot (Ligar_Bot.bat / python -m bot.phase0)
2. Chamar T212 API (demo) → obter estado do portfolio e posições abertas
3. Enriquecer dados com indicadores técnicos (RSI-14, EMA-50, EMA-200, volume ratio)
4. Gerar sinais de análise por ticker (sem submeter ordens)
5. Calcular snapshot de risco (equity, posições acima do limite)
6. Guardar relatório em data/beta/beta_analysis.json
7. Commit automático para GitHub (git push origin main)
8. Imprimir relatório em consola
```

**Nenhuma ordem é submetida nesta fase.** O bot apenas observa, analisa e sugere.

### 1.3 Lógica de Sinais ([[strategy|strategy.py]])

O bot usa dois tipos de sinais:

**Entradas (ENTRY) — condições obrigatórias:**

| Regra | Condição | Tipo |
|---|---|---|
| A — Oversold em uptrend | RSI ≤ 35 + [[atom-ema50|EMA-50]] > [[atom-ema200|EMA-200]] + volume ≥ 1.2× | Entrada clássica |
| B — Momentum surge | RSI 40–55 + [[atom-ema50|EMA-50]] > [[atom-ema200|EMA-200]] + volume ≥ 1.8× | Entrada por momentum |

**Saídas (EXIT / REDUCE):**

| Regra | Condição | Ação |
|---|---|---|
| C — Sobrecomprado | RSI ≥ 72 | EXIT — vender 100% |
| D — Inversão de tendência | [[atom-ema50|EMA-50]] < [[atom-ema200|EMA-200]] em posição aberta | REDUCE — vender 50% |

### 1.4 Gestão de Risco ([[config|config.py]] + [[strategy|strategy.py]])

Os parâmetros de risco atuais são:

| Parâmetro | Valor Atual |
|---|---|
| Posição máxima por ativo | 20% da equity |
| Exposição máxima por setor | 40% da equity |
| Perda diária máxima | 3% |
| Trades máximos por dia | 10 |
| Stop Loss | 5% |
| Take Profit | 10% |
| Dias sem trade antes de earnings | 2 |
| Mínimo de dados históricos | 20 pontos |

O modo `LIVE_TRADING = False` está hardcoded em `config.py`, garantindo que o bot nunca executa ordens reais sem alteração explícita.

### 1.5 Módulo de Aprendizagem ([[learner|learner.py]])

O `learner.py` analisa trades fechados dos últimos 7–30 dias e deteta 3 padrões de erro:

1. **low_volume_entry** — entradas com volume < 1× a média que resultaram em perda
2. **high_rsi_entry** — entradas com RSI > 65 que resultaram em perda
3. **counter_trend_buy** — compras com [[atom-ema50|EMA-50]] < [[atom-ema200|EMA-200]] que resultaram em perda

Gera sugestões de ajuste de parâmetros (`suggest_parameter_adjustments`) com base em win rate, rácio ganho/perda e padrões detetados. **Nenhum ajuste é aplicado automaticamente** — todos requerem aprovação manual em `config.py`.

---

## 2. Diagnóstico: O Que o Bot Faz Bem e o Que Falta

### 2.1 Pontos Fortes

- Arquitetura modular e limpa — fácil de estender
- Guardrails de risco sólidos (posição, perda diária, trades/dia)
- Sistema de logging de decisões com auditoria completa
- Modo demo seguro (`LIVE_TRADING = False`)
- Auto-commit para GitHub — histórico de análises preservado
- Learner não aplica mudanças sem aprovação humana — **filosofia correta**
- Indicadores técnicos bem escolhidos para Fase 1

### 2.2 Lacunas Identificadas (prioridade de desenvolvimento)

| Lacuna | Impacto | Prioridade |
|---|---|---|
| Sem watchlist dinâmica — o bot só analisa posições já abertas | Alto — não deteta oportunidades novas | Alta |
| Sem deteção de regime de mercado (bull/bear/lateral) | Alto — mesma estratégia aplicada em todos os contextos | Alta |
| Sem dados de setor/indústria por ativo | Médio — sem contexto setorial para filtrar | Média |
| Sem score de candidatos para entrada | Médio — sem ranking de oportunidades | Média |
| Sem integração de sentimento de notícias | Médio — sem contexto macro/micro por ativo | Média |
| [[learner|learner.py]] só deteta 3 padrões fixos | Baixo — padrões novos não são detetados | Baixa |
| Sem métricas de qualidade do backtest (Sharpe, [[MOC_CRO|drawdown]]) | Baixo — performance avaliada só por P&L | Baixa |

---

## 3. Princípios Estratégicos Definidos na Sessão

Esta secção compila os insights da sessão de estratégia com o proprietário.

### 3.1 Horizonte Temporal — Porquê 5–10 Dias e Não Horas

O rácio sinal/ruído nos mercados de curto prazo (minutos a horas) é dominado por movimentos aleatórios — ordens pontuais, spreads, emoção instantânea. À medida que o horizonte aumenta para 5–10 dias, o ruído médio-se e o sinal (tendência, momentum, fatores macro) torna-se mais detetável. Adicionalmente, menos operações implica menores custos acumulados de comissões e slippage, que destroem edges pequenos em alta frequência.

**Conclusão:** O bot deve ser treinado e avaliado em horizontes de 5–10 dias de holding period, não intraday.

### 3.2 Tamanho do Retorno Alvo

| Perfil de retorno | Vantagem | Risco principal |
|---|---|---|
| 0,5–1,5% frequente | Consistência, [[MOC_CRO|drawdown]] menor | Fees e slippage destroem edge; erro de execução multiplica-se |
| 1–3% moderado (alvo recomendado) | Frequência aceitável + edge sobrevive a custos | Requer edge estatística bem validada |
| 5–10% raro | Edge robusta por operação | Drawdowns longos; dependência de catalisadores |

**Conclusão:** O bot deve visar retornos de 1–3% por trade num horizonte de 5–10 dias. Os parâmetros atuais (stop 5%, [[MOC_Clyde|take profit]] 10%) estão alinhados com este objetivo, mas o [[MOC_Clyde|take profit]] pode ser reduzido para 7–8% para aumentar a taxa de concretização.

### 3.3 Long-Only em Fase 1, Short em Fase 2

O `strategy.py` atual já está corretamente configurado como long-only (`direction: Literal["LONG"]`). Short implica risco ilimitado teórico, custos de empréstimo, risco de short squeeze e lógica de risco assimétrica. A progressão recomendada é:

1. **Fase 1:** Long-only, estabilizar a estratégia
2. **Fase 2:** Proteção/redução de exposição em regimes de queda (já parcialmente presente via REDUCE)
3. **Fase 3 (futura):** Short seletivo com stops apertados, tamanho pequeno, apenas em catalisadores claros

### 3.4 Deteção de Regime de Mercado

A mesma estratégia de entrada tem resultados muito diferentes em mercado em tendência vs. lateral vs. queda abrupta. O bot precisa de um módulo de regime que classifique o estado do mercado antes de gerar sinais de entrada.

**Indicadores de regime sugeridos:**
- % de ações do S&P 500 acima da [[atom-ema200|EMA-200]] (breadth)
- Distância do SPY/QQQ à sua [[atom-ema200|EMA-200]]
- VIX ou [[atom-atr|ATR]] normalizado (volatilidade)
- Retorno do índice nos últimos 20 dias

**Estados de regime:**
- `bull_trending` — bot opera com parâmetros normais
- `bull_lateral` — bot reduz tamanho de posição, exige mais confirmação de volume
- `bear_correction` — bot só permite REDUCE/EXIT, sem novas entradas
- `bear_capitulation` — bot congela totalmente até regime mudar

### 3.5 Watchlist Dinâmica — Funil de Seleção

Em vez de analisar apenas posições abertas, o bot deve manter uma watchlist dinâmica de 15–30 ações, atualizada semanalmente, gerada por um funil de 4 camadas:

**Camada 1 — Setores elegíveis**
Escolher 5–8 setores com tendência positiva a médio prazo (performance 3–6 meses vs. SPY). Sugestão de setores base: Technology, Healthcare, Consumer Discretionary, Industrials, Energy.

**Camada 2 — Ranking por movimento recente**
Dentro de cada setor, ordenar por retorno das últimas 1–2 semanas. Selecionar top 10% (winners) e bottom 10% (losers).

**Camada 3 — Filtros de qualidade**
- Volume médio diário mínimo (ex: > $10M para ações americanas)
- Preço mínimo por ação (ex: > $5 — evitar penny stocks)
- Excluir ações a menos de 2 dias de earnings

**Camada 4 — Score composto e corte final**

O score de cada candidata é calculado como:

\[
\text{Score} = 0{,}4 \times \text{momentum\_1M} + 0{,}3 \times \text{momentum\_3M} + 0{,}2 \times \text{liquidez\_normalizada} + 0{,}1 \times \text{qualidade\_fundamental}
\]

Selecionar top 15–30 ações com maior score para a watchlist ativa.

**Frequência de atualização:**
- Score e ranking: **semanal**
- Reconstrução completa da watchlist: **mensal**

### 3.6 Features para o Modelo de Aprendizagem

O `learner.py` atual usa apenas 3 padrões fixos baseados em regras. Para evoluir para um modelo estatístico mais robusto, as features recomendadas são:

**Features técnicas (já parcialmente presentes):**
- [[atom-rsi14|RSI-14]], RSI-5 (curto prazo)
- [[atom-ema50|EMA-50]], [[atom-ema200|EMA-200]], crossover booleano
- Volume ratio vs. média 20 dias
- ATR-14 (volatilidade realizada)
- Retornos passados: 1d, 5d, 20d, 60d

**Features de contexto de mercado (a adicionar):**
- Regime atual (bull/bear/lateral — codificado como inteiro 0/1/2/3)
- Performance do setor da ação vs. SPY (1M, 3M)
- % de ações do setor acima da [[atom-ema200|EMA-200]]

**Features de sentimento (fase posterior):**
- Score de sentimento FinBERT agregado dos últimos 3 dias por ação
- Número de notícias negativas/positivas nas últimas 48h

**Target (o que o modelo aprende a prever):**
- Retorno a 5 dias (ou 10 dias) após a entrada
- Classificação binária: `retorno > 1.5%` → 1 (entrada válida), senão → 0

### 3.7 Validação Rigorosa — Anti-Overfitting

O maior risco de qualquer bot de trading é memorizar o passado em vez de aprender regras robustas. As medidas obrigatórias são:

1. **Walk-forward validation** — treinar em janela deslizante (ex: 6 meses de treino, 1 mês de teste, avançar 1 mês de cada vez)
2. **Sem look-ahead bias** — qualquer feature calculada no dia D só pode usar dados de D-1 para trás
3. **Out-of-sample test set** — reservar os últimos 6–12 meses de dados que nunca entram no treino
4. **Máximo 10–15 features** — mais features = mais overfitting
5. **Paper trading mínimo 30 dias** antes de qualquer live trading
6. **Modelo preferido para início:** XGBoost ou Random Forest (interpretáveis, robustos, menos propensos a overfitting que LSTM)

### 3.8 Rotina de Estudo do Mercado — Ritual Diário

Para complementar o bot com contexto humano:

**Diariamente (10–15 min):**
1. Verificar regime do mercado (SPY, QQQ, VIX)
2. Verificar quais setores estão a liderar/perder
3. Atualizar watchlist score (adicionar/remover candidatos óbvios)
4. Anotar 1–2 hipóteses para a semana

**Semanalmente (30 min):**
1. Rever o relatório semanal do `learner.py`
2. Avaliar sugestões de ajuste de parâmetros
3. Reconstruir watchlist com o funil completo
4. Aprovar (ou rejeitar) ajustes no `config.py`

---

## 4. Roadmap de Evolução Faseado

### Fase 0 — Observação (Estado Atual ✅)

- Bot lê dados da [[atom-trading212|T212]] demo
- Calcula RSI, EMA, volume para posições abertas
- Gera sugestões sem executar ordens
- Auto-commit para GitHub

**Duração recomendada:** Mínimo 2–4 semanas de observação contínua

### Fase 1 — Watchlist + Regime (A Implementar)

**Tarefas para o Claude Code:**

1. **Criar `watchlist_manager.py`**
   - Input: lista de setores e universo de ações (ex: S&P 500 via `yfinance`)
   - Output: lista de 15–30 candidatos com score calculado
   - Funções: `build_watchlist()`, `score_candidates()`, `filter_quality()`
   - Persistência: guardar em `data/beta/watchlist.json`, atualizado semanalmente

2. **Criar `regime_detector.py`**
   - Input: dados históricos do SPY/QQQ e breadth do mercado
   - Output: regime atual (`bull_trending`, `bull_lateral`, `bear_correction`, `bear_capitulation`)
   - Integração: `strategy.py` deve chamar `get_current_regime()` antes de gerar qualquer sinal de entrada

3. **Atualizar `strategy.py`**
   - Adicionar filtro de regime: sem ENTRY signals se regime = `bear_correction` ou `bear_capitulation`
   - Reduzir tamanho de posição em `bull_lateral` (multiplicar `size_eur` por 0.6)
   - Adicionar filtro: sem ENTRY se ação tem earnings nos próximos 2 dias (já existe `no_trade_before_earnings_days` em config)

4. **Atualizar `phase0.py`**
   - Expandir a análise para incluir a watchlist (não apenas posições abertas)
   - Incluir regime atual no relatório
   - Incluir top 5 candidatos da watchlist no relatório

5. **Atualizar `config.py`**
   - Adicionar parâmetros da watchlist:
     ```python
     WATCHLIST_CONFIG = {
         "max_size": 25,
         "sectors": ["XLK", "XLV", "XLY", "XLI", "XLE"],
         "min_avg_volume_usd": 10_000_000,
         "min_price_usd": 5.0,
         "score_weights": {"momentum_1m": 0.4, "momentum_3m": 0.3, "liquidity": 0.2, "quality": 0.1},
         "update_frequency_days": 7,
     }
     ```
   - Adicionar parâmetros de regime:
     ```python
     REGIME_CONFIG = {
         "bear_threshold_spy_ema200_pct": -5.0,
         "bull_breadth_threshold_pct": 60.0,
         "lateral_atr_multiplier": 0.8,
     }
     ```

**Duração recomendada desta fase:** 2–4 semanas em modo paper

### Fase 2 — Modelo de Aprendizagem Estatístico (Futura)

**Tarefas para o Claude Code:**

1. **Criar `feature_builder.py`**
   - Calcular todas as features descritas na secção 3.6
   - Garantir zero look-ahead bias (função `shift(1)` em todos os dados do dia atual)
   - Output: `pd.DataFrame` com features + target para treino

2. **Criar `model_trainer.py`**
   - Implementar walk-forward validation com janela de 6 meses treino + 1 mês teste
   - Modelo base: XGBoost com `n_estimators=200`, `max_depth=4`, `learning_rate=0.05`
   - Guardar modelo em `data/models/model_vX.pkl`
   - Métricas obrigatórias: accuracy, precision, recall, Sharpe ratio simulado, max [[MOC_CRO|drawdown]]

3. **Integrar modelo em `strategy.py`**
   - Criar `_ml_entry_signal()` que usa o modelo treinado como filtro adicional sobre as regras existentes
   - As regras rule-based (RSI, EMA, volume) continuam a ser necessárias — o modelo é um filtro extra, não um substituto
   - Threshold de confiança mínima: 0.60 (o modelo só aprova entrada se probabilidade > 60%)

4. **Atualizar `learner.py`**
   - Adicionar `retrain_model()` que re-treina o modelo com os trades mais recentes
   - Adicionar métricas de qualidade do modelo ao relatório semanal
   - **Regra:** re-treino só é ativado manualmente ou após aprovação explícita

**Duração recomendada desta fase:** 1–2 meses de paper trading com modelo

### Fase 3 — Live Trading Controlado (Futura)

**Condições obrigatórias antes de ativar `LIVE_TRADING = True`:**

- [ ] Paper trading com Fase 1 + Fase 2 por mínimo 30 dias
- [ ] Win rate > 50% em paper trading
- [ ] Rácio ganho médio / perda média > 1.5
- [ ] Nenhum dia de perda > 3% em paper trading
- [ ] Walk-forward validation com Sharpe > 0.8
- [ ] Aprovação manual explícita no `config.py`

**Parâmetros de arranque live (conservadores):**

```python
RISK_CONFIG_LIVE_CONSERVATIVE = {
    "max_position_pct": 10.0,     # metade do valor atual
    "max_sector_pct": 25.0,
    "max_daily_loss_pct": 2.0,    # mais apertado
    "max_trades_per_day": 3,      # muito restrito no início
    "stop_loss_pct": 4.0,
    "take_profit_pct": 8.0,
}
```

---

## 5. Factores de Risco — O Que Pode Correr Mal

| Risco | Descrição | Mitigação Atual | Mitigação Proposta |
|---|---|---|---|
| Overfitting | Bot memoriza o passado e falha em mercado real | [[learner|learner.py]] não aplica mudanças sozinho | Walk-forward validation obrigatório |
| Mudança de regime | Bull market → [[MOC_CRO|bear]]; estratégia deixa de funcionar | REDUCE em EMA cross | Módulo de regime explícito |
| Custos de transação | Fees e spread corroem edge de 0.5–1.5% | — | Visar retornos ≥ 1.5% por trade |
| Look-ahead bias | Features calculadas com dados futuros no backtest | — | `shift(1)` obrigatório em feature_builder |
| Slippage | Execução real pior que simulada | MARKET orders | Usar LIMIT orders com offset pequeno |
| Earnings surprises | Resultados inesperados causam gaps bruscos | `no_trade_before_earnings_days: 2` | Verificação ativa de calendário de earnings |
| API indisponível | [[atom-trading212|T212]] API em baixo → bot sem dados | Abort com log | Retry com backoff exponencial |
| Concentração excessiva | Muita exposição num setor | `max_sector_pct: 40%` | Adicionar filtro de correlação entre posições |
| Short squeeze (Fase 3) | Posições short expostas a subidas explosivas | Long-only em Fase 1 | Stops muito apertados em short; posições pequenas |

---

## 6. Factores que Potenciam os Ganhos

| Fator | Como Implementar | Impacto Esperado |
|---|---|---|
| Watchlist dinâmica por setor | `watchlist_manager.py` com score semanal | Mais oportunidades de qualidade; menos ruído |
| Filtro de regime | `regime_detector.py` integrado em `strategy.py` | Evitar entradas em mercado adverso |
| Força relativa setorial | Feature: retorno setor vs. SPY a 1M e 3M | Focar em ações de setores em liderança |
| Sentimento de notícias (FinBERT) | Score de sentimento como feature no modelo | Capturar catalisadores não refletidos no técnico |
| Walk-forward + out-of-sample | `model_trainer.py` com validação rigorosa | Modelo mais robusto; menos overfitting |
| Gestão de posição dinâmica | Aumentar posição em sinais de alta confiança | Melhor aproveitamento das melhores oportunidades |
| Calendário de earnings | Evitar entrar 2+ dias antes de resultados | Reduz gap risk |
| Score de momentum multi-timeframe | Features de retorno 5d, 20d, 60d | Melhor alinhamento com tendência de fundo |

---

## 7. Instruções Específicas para o Claude Code

### Prioridade 1 — Implementar Agora (para modo de observação contínua)

1. Criar `bot/watchlist_manager.py` com as funções `build_watchlist()`, `score_candidates()`, `filter_quality()` usando `yfinance` como fonte de dados. A watchlist deve ser guardada em `data/beta/watchlist.json` e atualizada automaticamente se o ficheiro tiver mais de 7 dias.

2. Criar `bot/regime_detector.py` com a função `get_current_regime()` que retorna um dos 4 estados de regime com base em dados do SPY/QQQ via `yfinance`. Guardar o regime atual em `data/beta/regime.json`.

3. Atualizar `bot/phase0.py` para incluir na análise diária: (a) regime atual, (b) top 10 candidatos da watchlist com score, (c) alerta se regime = bear.

4. Atualizar `bot/config.py` para incluir `WATCHLIST_CONFIG` e `REGIME_CONFIG` conforme definido na secção 4 deste relatório.

5. Garantir que `phase0.py` corre sem erros quando não há posições abertas (o bot deve ser capaz de correr "a frio" com portfolio vazio).

### Prioridade 2 — Implementar Após 2–4 Semanas de Observação

6. Criar `bot/feature_builder.py` com todas as features da secção 3.6, com garantia de zero look-ahead bias.

7. Criar `bot/model_trainer.py` com XGBoost + walk-forward validation. Guardar modelo em `data/models/`.

8. Integrar modelo em `strategy.py` como filtro adicional (não substituição das regras rule-based).

9. Atualizar `learner.py` para incluir métricas de qualidade do modelo no relatório semanal.

### Prioridade 3 — Só Depois de Aprovação Humana Explícita

10. Alterar `LIVE_TRADING = True` em `config.py` e substituir `RISK_CONFIG` por `RISK_CONFIG_LIVE_CONSERVATIVE` (secção 4, Fase 3).

11. Atualizar `execution.py` para suportar LIMIT orders com offset de 0.1–0.2% em vez de MARKET orders para reduzir slippage.

---

## 8. Checklist de Saúde do Bot (para revisão semanal pelo proprietário)

Após cada semana de observação, verificar:

- [ ] `data/beta/beta_analysis.json` foi atualizado nas últimas 24h
- [ ] `data/beta/beta_weekly_report.txt` existe e foi gerado
- [ ] `logs/trades/` contém ficheiros com datas recentes
- [ ] Regime atual está registado em `data/beta/regime.json`
- [ ] Watchlist tem entre 15–30 ações (`data/beta/watchlist.json`)
- [ ] Nenhum erro crítico em `logs/errors/`
- [ ] Win rate das últimas 2 semanas está acima de 45%
- [ ] Rácio ganho médio / perda média está acima de 1.0
- [ ] Nenhuma posição excede 20% da equity
- [ ] `learner.py` não fez nenhum ajuste automático sem aprovação

---

*Este relatório foi gerado com base na análise do código-fonte do repositório `chico130/fundscope` e na sessão de estratégia de Maio de 2026. Todos os ajustes de parâmetros e ativação de live trading requerem aprovação explícita do proprietário.*
