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
ultima_revisao: 2026-05-23
---
# FundScope Bot — Relatório de Diagnóstico, Estratégia e Roadmap de Evolução

Retorno: [[MOC_FundScope]]

> **Destinatário:** Claude Code (configuração e desenvolvimento do bot)
> **Fonte:** Análise do repositório `chico130/fundscope` + sessão de estratégia com o proprietário
> **Data inicial:** Maio 2026  ·  **Última actualização:** 2026-05-23
> **Objetivo:** Explicar a dinâmica atual do bot, compilar os insights da sessão de estratégia, e definir um plano de evolução faseado para passar de "modo observação" a "modo autónomo com aprovação humana".
>
> ⚠️ **Aviso sobre drift de spec:** A versão original deste documento (Maio 2026) descrevia o bot como estando em "Fase 0 só-leitura". A 2026-05-23 confirmou-se que o bot já corre Fase 1 (execução automática) e implementou parcialmente a Fase 2 (modelo Bonnie). As secções 1, 4 e a nova secção 9 foram revistas para reflectir o estado real. Antes de planear novos upgrades, ler a §9 (Estado Real de Implementação).

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

### 1.2 Dinâmica de Funcionamento (Fase 1 — Estado Atual 2026-05-23)

O bot está actualmente em **Fase 1 (execução automática em demo)**.
Confirmado por `PHASE1_EXECUTION = True` em [[config|config.py]] e por trades
reais no `beta_trades.json`. O ciclo `phase0.run()` (15 min em CI, ou contínuo
via [[VPS_MIGRATION_SPEC|VPS]]) executa:

```
1. _sync_from_t212_strict() → GET T212 portfolio+cash, reconcilia ledger
2. reconcile_orphan_buy_orders() → cancela BUYs órfãs de fechamentos antigos
3. enrich_with_technicals() → RSI-14, EMA-20/50/200, ATR-14, vol ratio,
   rs_bullish (vs SPY)
4. regime_detector.get_current_regime() → 4 estados (bull_trending,
   bull_lateral, bear_correction, bear_capitulation)
5. _scan_watchlist_candidates() → 100 tickers × generate_signals (throttled)
6. bonnie.filter_proposals() → ML model + thresholds vetam entradas fracas
7. _apply_social_veto() → veto por sentimento Reddit / divergência analistas
8. exit_manager.check_exit_barriers() → ATR Three Barriers (SL, BE trigger, target)
9. _execute_phase1() → submete BUYs/SELLs via api_client (gate: mercado aberto)
10. cro.observe()/interpret()/speak() → escreve cro_insights.json
11. learner.run_learner_cycle() → analisa trades fechados, propõe parâmetros
12. Telegram via notifier (oportunidades, vetos, kill switches, despertar/boa noite)
13. git_sync → commit + push de relatórios e logs
```

**Ordens submetidas em conta demo T212.** `LIVE_TRADING = False` impede flip
para conta real.

### 1.3 Lógica de Sinais ([[strategy|strategy.py]]) — actual 2026-05-23

Os sinais são parametrizados pelo Learner (defaults em [[learner|learner.py]],
overridable em runtime). Estilos activos: `VALUE` e `MOMENTUM`.

**Entradas (ENTRY) — três regras:**

| Regra | Estilo | Condição (parâmetros do Learner em itálico) |
|---|---|---|
| A — Sobrevendido em uptrend | VALUE | RSI ≤ *34* + EMA-50 > EMA-200 + vol ≥ *1.2×* |
| B — Momentum neutro + volume | VALUE | RSI *40–55* + EMA-50 > EMA-200 + vol ≥ *1.8×* |
| M — Breakout momentum | MOMENTUM | RSI ≥ *58* + price > EMA-20 > EMA-50 > EMA-200 (alinhamento 4 EMAs) + vol ≥ *1.5×* |

**Saídas (EXIT / REDUCE) — por estilo da posição:**

| Regra | Estilo da posição | Condição | Acção |
|---|---|---|---|
| C — Sobrecomprado | VALUE | RSI ≥ *72* | EXIT 100% |
| D — Inversão de tendência | VALUE | EMA-50 < EMA-200 | REDUCE 50% |
| E — ATR Trailing Stop | MOMENTUM | close < peak_high − *2.5*×ATR-14 | EXIT 100% |

Adicionalmente o `exit_manager` corre Three Barriers ATR (stop loss a entrada−1.5×ATR,
break-even trigger a +1×ATR, target a +3×ATR) para todos os trades com barreiras
armazenadas no momento da entrada.

### 1.4 Gestão de Risco ([[config|config.py]] + [[strategy|strategy.py]]) — actual

```python
RISK_CONFIG = {
    "max_position_pct": 20.0,        # % máxima por ticker
    "max_sector_pct": 40.0,
    "max_daily_loss_pct": 3.0,
    "max_trades_per_day": 10,
    "stop_loss_pct": 5.0,
    "take_profit_pct": 10.0,
    "no_trade_before_earnings_days": 2,
    "min_data_points_required": 20,
    "max_positions_per_sector": 2,   # NOVO vs spec original
}
```

Acrescentos não previstos na spec original:
- **Position sizing ATR** ([[cro.py]]): tamanho € por trade é função do ATR e
  do risco-alvo (1% da equity por trade). Substitui o factor fixo
  `tamanho_maximo_posicao`.
- **Multiplicadores de regime** ([[cro.py]]): `bull_trending` 1.0×,
  `bull_lateral` 0.5×, `bear_correction`/`bear_capitulation` 0.0×
  (`bear_value_multiplier` 0.25× para sinais VALUE em bear).
- **Bonnie thresholds dinâmicos**: base 0.60, strict 0.64 quando WR(25) < 0.45.

`LIVE_TRADING = False` hardcoded — garante que nenhuma ordem chega à conta real.

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

> **Estado a 2026-05-23**: Fase 0 ✅, Fase 1 ✅, Fase 2 🟡 parcial (ver §9),
> Fase 3 ⏳ não iniciada. Esta secção descreve o plano original — os deltas
> entre plano e implementação real estão consolidados na nova §9.

### Fase 0 — Observação ✅ COMPLETA

- Bot lê dados da [[atom-trading212|T212]] demo
- Calcula RSI, EMA, volume para posições abertas
- Gera sugestões sem executar ordens
- Auto-commit para GitHub

### Fase 1 — Watchlist + Regime ✅ COMPLETA + EXPANDIDA

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

### Fase 2 — Modelo de Aprendizagem Estatístico 🟡 PARCIAL (ver §9)

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

### Fase 3 — Live Trading Controlado ⏳ NÃO INICIADA (pré-requisitos abertos — ver §9)

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

---

## 9. Estado Real de Implementação (snapshot 2026-05-23)

Esta secção é a fonte de verdade actual. As secções 1–8 acima foram escritas em
Maio de 2026 e descrevem o plano; algumas afirmações (especialmente sobre o estado
do bot) deixaram de bater certo com o código real. Os dados abaixo foram extraídos
directamente dos módulos em execução.

### 9.1 Fases — estado de implementação

| Fase | Plano (§4) | Estado real | Notas |
|---|---|---|---|
| 0 — Observação | só leitura | ✅ feito | Substituído pela Fase 1 |
| 1 — Watchlist + Regime | 25 tickers, sem exec | ✅ feito + **expandido** | 100 tickers, refresh diário, execução automática |
| 2 — ML Bonnie | XGBoost, 10-15 features, walk-forward | 🟡 **parcial** | GradientBoosting, 4 features, StratifiedKFold |
| 3 — Live Trading | LIVE_TRADING=True + conservative config | ⏳ não iniciada | `RISK_CONFIG_LIVE_CONSERVATIVE` não existe no código |

### 9.2 Fase 1 — divergências entre spec e realidade

| Spec original (§4 Fase 1) | Realidade actual ([[config|config.py]]) |
|---|---|
| `WATCHLIST_CONFIG.max_size = 25` | **100** |
| `WATCHLIST_CONFIG.update_frequency_days = 7` | **1** (diário) |
| `WATCHLIST_CONFIG.sectors = 5` ("XLK, XLV, XLY, XLI, XLE") | **9** (+ XLF, XLC, XLU, XLP) |
| `REGIME_CONFIG.bear_threshold_spy_ema200_pct = -5.0` | **0.0** |
| Apenas estilo VALUE (regras A, B) | **VALUE + MOMENTUM** (regras A, B, M) |
| Sem ATR Trailing Stop | **Three Barriers ATR** + MOMENTUM Trailing |
| `no_trade_before_earnings_days = 2` | igual ✅ |
| Position sizing fixo via `tamanho_maximo_posicao` | **ATR sizing** via [[cro.py]] (1% risk-target) |

A maioria das diferenças foram acrescentos, não regressões — a watchlist mais
larga e o estilo MOMENTUM são extensões legítimas da spec inicial.

### 9.3 Fase 2 — o que está feito (e o que NÃO está)

**Implementado:**

| Componente | Ficheiro | Estado |
|---|---|---|
| Captura de observações | [[mass_backtest|bot/mass_backtest.py]] | ✅ gera `data/backtest/bonnie_observations.json` |
| Feature builder | [[feature_builder|bot/feature_builder.py]] | ✅ 4 features (rsi_14, ema50_above_200, vol_ratio, regime) |
| Treino de modelo | [[model_trainer|bot/model_trainer.py]] | ✅ `GradientBoostingClassifier` (sklearn) |
| Modelo persistido | `data/models/bonnie_model.pkl` | ✅ presente |
| Integração no pipeline | [[bonnie.py]] `filter_proposals()` | ✅ veto/aprovação por força do sinal |

**Não implementado (vs §4 Fase 2):**

| Requisito da spec | Estado | Comentário |
|---|---|---|
| Modelo base **XGBoost** | ❌ | usa `GradientBoostingClassifier` (sklearn) — design choice diferente |
| **10–15 features** | ❌ | usa 4 — datasets pequenos da demo não suportam mais sem overfit |
| **Walk-forward validation** | ❌ | usa `StratifiedKFold` cross-validation |
| **Out-of-sample test set** (últimos 6-12 meses retidos) | ❌ | não implementado |
| **Sharpe ratio / max drawdown** nas métricas de treino | ❌ | só accuracy + feature importance |
| **Look-ahead bias check automatizado** | ❌ | confiamos no pipeline manual sem teste |
| Threshold 0.60 | ✅ | `bonnie.base_threshold = 0.60` |
| Re-treino manual aprovado | ✅ | `model_trainer.train()` corrido a pedido |

**Decisão pendente:** queremos completar a Fase 2 conforme a spec original
(reescrever para XGBoost + walk-forward), ou consolidar o que está e refinar?
A escolha depende do tamanho do dataset de `bonnie_observations.json` —
se < ~500 observações, XGBoost over-fitter-á e o ganho será zero.

### 9.4 Fase 3 — pré-requisitos abertos

A spec da Fase 3 (§4) lista checks que ainda não estão satisfeitos. Para flip
de `LIVE_TRADING = True` precisas de:

| Pré-requisito da spec | Estado |
|---|---|
| Paper trading ≥ 30 dias | em curso (demo desde Mai 2026) |
| Win rate > 50% em paper | medir com `learner.run_learner_cycle()` |
| Rácio ganho médio / perda média > 1.5 | medir |
| Nenhum dia de perda > 3% em paper | verificar `data/beta/beta_equity.json` |
| Walk-forward Sharpe > 0.8 | **bloqueado** — walk-forward ainda não existe (§9.3) |
| Aprovação manual explícita | requer alterar `LIVE_TRADING` em [[config.py]] |

**Ausente do código mas exigido pela spec:**

```python
# NÃO existe em config.py — tem de ser adicionado antes do flip
RISK_CONFIG_LIVE_CONSERVATIVE = {
    "max_position_pct": 10.0,     # metade do demo
    "max_sector_pct": 25.0,
    "max_daily_loss_pct": 2.0,
    "max_trades_per_day": 3,
    "stop_loss_pct": 4.0,
    "take_profit_pct": 8.0,
}
```

**Risco técnico adicional (descoberto 2026-05-23):** o schema da API live
pode diferir do demo. Antes do flip:

1. Renovar `T212_API_ID/KEY` para credenciais live
2. Apontar `T212_BASE_URL` para `https://live.trading212.com/api/v0`
3. Correr `python scripts/t212_contract_test.py` **contra live** — exige
   13/13 passes
4. Se algum dos 13 falhar, há diferenças schema que precisam de uma secção
   "Live differences" em [[docs/T212_API_MANUAL.md]] antes de continuar

### 9.5 Defesa contra futuros bugs como o do `timeValidity`

A 23-mai descobriu-se que [[docs/T212_API_MANUAL.md]] tinha 3 afirmações
falsas que ficaram codificadas em [[api_client.py]] durante meses. Para
evitar reincidência em qualquer upgrade futuro:

- **Contract test** ([[scripts/t212_contract_test.py]]) tem 13 asserts
  empíricos contra a API demo. Correr antes de:
  - qualquer mudança em [[api_client.py]]
  - qualquer flip de fase (especialmente Fase 3)
  - qualquer release relevante
- **Manual T212 reescrito** com schema validado e secção §6 anti-regressão
  listando explicitamente os endpoints que NÃO funcionam.
- **Logging detalhado em `_post`** captura o body do response (não só o status
  code) — qualquer 400 inesperado fica diagnosticável directamente no log.

Estas três defesas são genéricas e devem ser mantidas em todas as fases
futuras. Não removas o contract test mesmo que pareça redundante — é o único
sentinel que avisa quando a T212 muda algo silenciosamente.
