---
id: spec-global
title: "FundScope — Especificação Técnica Global"
type: spec
domain: geral
regime: n/a
tags: [spec, global, arquitetura]
links_obrigatorios:
  parent_moc: "[[MOC_FundScope]]"
  vizinhos: "[[MOC_Clyde]] [[MOC_Bonnie]] [[MOC_CRO]]"
status: stable
ultima_revisao: 2026-05-19
---
# FundScope – Especificação Completa para Claude Code
**Versão:** 0.1 | **Data:** 2026-05-13 | **Autor:** chico130

---

## Contexto do Projecto

O FundScope é um site pessoal de monitorização de portfólio e trading, alojado em GitHub Pages.
Está dividido em duas contas/camadas:

- **ALFA** – Conta real ISA/Invest (Trading212 live), gerida manualmente.
- **BETA** – Bot de trading automatizado, a correr em dinheiro virtual (Trading212 demo/CFD paper trading).

O objectivo é ter as duas secções lado a lado na aba "Portfólio" do site, com comparação directa de desempenho.

---

## Estrutura de ficheiros do projecto

```
fundscope/
├── index.html                  # Página principal
├── markets.html                # Mercados
├── news.html                   # Notícias
├── portfolio.html              # Portfólio (ALFA + BETA)
│
├── data/
│   ├── alpha/
│   │   └── portfolio.json      # Dados reais ALFA (já existe)
│   └── beta/
│       ├── beta_summary.json   # KPIs do bot demo
│       ├── beta_positions.json # Posições actuais demo
│       ├── beta_equity.json    # Curva de equity demo
│       └── beta_trades.json    # Diário de trades com explicações
│
├── bot/
│   ├── config.py               # Configurações, limites de risco, flags demo/live
│   ├── api_client.py           # Camada de comunicação com T212 API
│   ├── data_layer.py           # Leitura de posições, preços, histórico
│   ├── strategy.py             # Lógica de regras e sinais
│   ├── execution.py            # Envio de ordens (demo first, live flag off)
│   ├── logger.py               # Logging estruturado de decisões e resultados
│   ├── learner.py              # Análise de erros e sugestão de ajustes
│   ├── reporter.py             # Gera JSON para o site (beta_summary, etc.)
│   └── main.py                 # Loop principal do bot
│
└── logs/
    ├── trades/                 # Um ficheiro por dia de trades
    ├── errors/                 # Erros e anomalias
    └── strategy_versions.json  # Histórico de versões da estratégia
```

---

## SECÇÃO ALFA – Conta real ISA/Invest

### Conceito
- Representa a carteira real do utilizador, gerida manualmente.
- Dados vindos de `data/alpha/portfolio.json` (pipeline já existente).
- Serve como **baseline** para avaliar o BETA.
- Nenhum automatismo de trading aqui — só monitorização e visualização.

### Estrutura `data/alpha/portfolio.json`

```json
{
  "updated": "2026-05-13T18:30:00Z",
  "t212_mode": "live",
  "summary": {
    "total_value": 9434.50,
    "total_invested": 6500.00,
    "total_gain_eur": 2934.50,
    "total_gain_pct": 45.15,
    "daily_gain_eur": 123.40,
    "daily_gain_pct": 1.3,
    "n_positions": 7
  },
  "positions": [
    {
      "ticker": "VRT",
      "ticker_display": "VRT",
      "display_name": "Vertiv Holdings",
      "quantity": 21,
      "avg_price": 50.00,
      "last_price": 99.15,
      "invested": 1050.00,
      "value": 2082.15,
      "gain_eur": 1032.15,
      "gain_pct": 98.30,
      "change_pct": 1.2,
      "allocation_pct": 22.1
    }
  ],
  "history": [
    { "date": "2026-04-01", "value": 7000.00 }
  ]
}
```

### Regras de cor obrigatórias (UI)
- `gain_eur >= 0` → classe CSS `up` → verde (`--color-success`)
- `gain_eur < 0`  → classe CSS `down` → vermelho (`--color-notification`)
- Esta regra aplica-se SEMPRE a todos os campos numéricos E percentuais de P&L.
- Nunca usar cores diferentes para o valor € e para a % da mesma métrica.

---

## SECÇÃO BETA – Bot de trading (Demo / Paper Trading)

### Conceito
- Ambiente de laboratório: dinheiro virtual, Trading212 demo/CFD paper.
- O bot opera autonomamente, mas com regras de risco rígidas.
- Aprende com os erros via análise de logs e ajuste de parâmetros.
- Toda a actividade é auditável e explicável (cada decisão tem motivo registado).
- Visualmente separado de ALFA — sempre com label "DEMO / PAPER TRADING".

### Ficheiro `data/beta/beta_summary.json`

```json
{
  "updated": "2026-05-13T18:35:00Z",
  "env": "demo",
  "strategy_version": "v0.1.0",
  "summary": {
    "initial_capital": 10000.00,
    "current_value": 10850.00,
    "total_gain_eur": 850.00,
    "total_gain_pct": 8.5,
    "max_drawdown_pct": -4.2,
    "n_trades": 57,
    "win_rate_pct": 62.0,
    "avg_win_eur": 45.00,
    "avg_loss_eur": -30.00,
    "best_trade_eur": 210.00,
    "worst_trade_eur": -95.00
  },
  "risk_limits": {
    "max_position_pct": 20.0,
    "max_daily_loss_pct": 3.0,
    "max_trades_per_day": 10
  }
}
```

### Ficheiro `data/beta/beta_positions.json`

```json
{
  "updated": "2026-05-13T18:35:00Z",
  "positions": [
    {
      "ticker": "VST",
      "display_name": "Vistra Corp",
      "quantity": 30,
      "avg_price": 30.00,
      "last_price": 32.20,
      "invested": 900.00,
      "value": 966.00,
      "gain_eur": 66.00,
      "gain_pct": 7.3,
      "change_pct": 0.8,
      "allocation_pct": 9.5
    }
  ]
}
```

### Ficheiro `data/beta/beta_equity.json`

```json
{
  "history": [
    { "datetime": "2026-05-01T09:00:00Z", "equity": 10000.00 },
    { "datetime": "2026-05-01T16:00:00Z", "equity": 10050.00 },
    { "datetime": "2026-05-02T16:00:00Z", "equity": 10020.00 }
  ]
}
```

### Ficheiro `data/beta/beta_trades.json`

```json
{
  "trades": [
    {
      "id": "2026-05-13T10:15:30Z_VRT_BUY",
      "datetime": "2026-05-13T10:15:30Z",
      "ticker": "VRT",
      "side": "BUY",
      "qty": 5,
      "price": 98.00,
      "env": "demo",
      "strategy_version": "v0.1.0",
      "reason": "Breakout acima da resistência recente, volume acima da média, dentro dos limites de risco.",
      "context": {
        "rsi_14": 58.3,
        "ema50_above_ema200": true,
        "volume_ratio_vs_avg": 1.45
      },
      "result_eur": null,
      "result_pct": null,
      "result_after_minutes": 60,
      "closed_at": null,
      "postmortem": null
    }
  ]
}
```

**Nota:** `result_eur`, `result_pct`, `closed_at` e `postmortem` são preenchidos mais tarde pelo bot quando a posição fechar ou o tempo expirar.

---

## Mandato do Bot (BETA)

### Princípios de Design

1. **Segurança primeiro, liberdade depois**
   - O bot nunca excede os limites de risco definidos em `config.py`.
   - Em caso de dúvida ou falta de dados, a acção default é **não fazer nada**.

2. **Aprendizagem contínua, mas controlada**
   - Regista todas as decisões com contexto e motivo.
   - Analisa periodicamente os logs para detectar padrões de erro.
   - Qualquer alteração à estratégia é documentada em `logs/strategy_versions.json`.
   - Nunca se "reinventa" silenciosamente — todas as mudanças têm motivo registado.

3. **Explicabilidade obrigatória**
   - Para cada trade: explicação de entrada e post-mortem após fecho.
   - Para cada erro recorrente: análise estruturada do porquê.

4. **Limitações de dados respeitadas**
   - Se a API não responde ou latência é alta → modo conservação (sem novas ordens).
   - Não é HFT; funciona bem com granularidade de 1–5 minutos.

### Fases de Evolução

| Fase | Descrição | Estado |
|------|-----------|--------|
| **Fase 0** | Só leitura + análise + sugestões em texto (sem ordens) | Implementar primeiro |
| **Fase 1** | Execução em demo com regras simples + logging | A seguir |
| **Fase 2** | Análise de logs + ajuste de parâmetros + relatório de erros | Depois |
| **Fase 3** | Modo real limitado (supervisão humana, plafond baixo) | Futuro |

### Limites de Risco (config.py)

```python
RISK_CONFIG = {
    "max_position_pct": 20.0,      # % máxima do portfólio por activo
    "max_sector_pct": 40.0,        # % máxima por sector/tema
    "max_daily_loss_pct": 3.0,     # % perda máxima diária (demo)
    "max_trades_per_day": 10,      # nº máximo de trades/dia
    "stop_loss_pct": 5.0,          # stop loss por trade
    "take_profit_pct": 10.0,       # take profit por trade (ajustável)
    "no_trade_before_earnings_days": 2,  # janela de bloqueio pre-earnings
    "min_data_points_required": 20,      # mín. pontos históricos para agir
}
```

### Módulos que o Claude Code deve implementar

#### `bot/api_client.py`
```python
# Funções principais:
def get_portfolio_state_demo()     # Lê posições e saldo da conta demo T212
def get_market_snapshot(tickers)   # Preços actuais dos tickers
def get_historical_data(ticker, days)  # Dados históricos (OHLCV)
def place_order_demo(ticker, side, qty, order_type, price=None)
def cancel_order_demo(order_id)
```

#### `bot/strategy.py`
```python
# Funções principais:
def check_risk_limits(proposed_trade, portfolio_state) -> bool
def generate_signals(market_data, portfolio_state) -> list[Signal]
def propose_trades(signals, portfolio_state) -> list[ProposedTrade]
```

#### `bot/logger.py`
```python
# Funções principais:
def log_trade(trade_dict)          # Regista trade com contexto
def log_decision(reason, action)   # Regista qualquer decisão
def log_error(error_type, detail)  # Regista erros e anomalias
def update_postmortem(trade_id, result_eur, result_pct, explanation)
```

#### `bot/learner.py`
```python
# Funções principais:
def analyse_recent_trades(days=7)   # Analisa trades recentes
def detect_error_patterns()         # Padrões de erro recorrentes
def suggest_parameter_adjustments() # Propõe ajustes com justificação
def generate_weekly_report()        # Relatório em texto: o que correu bem/mal
```

#### `bot/reporter.py`
```python
# Funções principais:
def update_beta_summary()    # Escreve data/beta/beta_summary.json
def update_beta_positions()  # Escreve data/beta/beta_positions.json
def update_beta_equity()     # Actualiza curva de equity
def update_beta_trades()     # Actualiza diário de trades
```

---

## UI – Separação Visual ALFA / BETA no site

### Estrutura da aba "Portfólio"

```
[TABS: ALFA ●  |  BETA (Demo) ●]

--- ALFA (quando activo) ---
KPIs: Valor Total | Investido | P&L Total | Hoje
Gráfico: Evolução do Portfólio (histórico real)
Tabela de Posições (dados reais)
Performance por Posição

--- BETA (quando activo) ---
Badge: ⚗️ DEMO / PAPER TRADING — dinheiro virtual
KPIs: Capital Demo | P&L Demo | Drawdown Máx | Win Rate
Gráfico: Equity Curve do Bot
Tabela de Posições Demo
Diário de Trades (com explicações do bot)
Comparação vs ALFA: "+X% vs conta real"
```

### Regras visuais obrigatórias

- BETA deve ter sempre um badge/label visível "DEMO – Dinheiro Virtual".
- Cores de P&L: mesma regra de ALFA (verde = ganho, vermelho = perda, sempre consistente entre valor € e %).
- Componentes reutilizados de ALFA, mas com prop/flag `mode="alpha" | "beta"`.

---

## Comparação ALFA vs BETA

O site deve mostrar um cartão de comparação com:

```json
{
  "alpha_gain_pct": 45.15,
  "beta_gain_pct": 8.5,
  "alpha_drawdown_pct": -12.0,
  "beta_drawdown_pct": -4.2,
  "period_start": "2026-05-01",
  "period_end": "2026-05-13",
  "verdict": "ALFA supera BETA neste período | BETA supera ALFA neste período"
}
```

---

## Próximos Passos (por ordem de prioridade)

1. [ ] Criar estrutura de pastas `data/alpha/`, `data/beta/`, `bot/`, `logs/`.
2. [ ] Criar ficheiros JSON Beta com dados mock (para o site já mostrar a secção).
3. [ ] Implementar `bot/api_client.py` com credenciais demo T212.
4. [ ] Implementar `bot/data_layer.py` e `bot/logger.py`.
5. [ ] Implementar Fase 0: só leitura + análise + sugestões em texto.
6. [ ] Actualizar `portfolio.html` com tabs ALFA/BETA e consumo dos JSON Beta.
7. [ ] Implementar `bot/strategy.py` com regras simples (Fase 1).
8. [ ] Implementar `bot/learner.py` e `bot/reporter.py` (Fase 2).

---

## Notas finais para o Claude Code

- Linguagem preferida: **Python** (bot) + **HTML/CSS/JS vanilla** (front-end).
- Sem frameworks pesados no front-end (já usa Chart.js + CSS puro).
- Todos os ficheiros JSON do Beta devem ser escritos pelo bot e lidos pelo front-end (comunicação assíncrona simples via ficheiros).
- O bot corre no PC do utilizador (não numa cloud por agora).
- A API da Trading212 tem rate limits — o bot deve respeitar pausas entre chamadas.
- **Demo first, always.** A flag `LIVE_TRADING = False` em `config.py` deve estar sempre desligada até testes extensivos em demo.

