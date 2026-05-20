---
id: spec-sp500-backtest
title: "Calibração Quantitativa Offline — Sweep de Parâmetros sobre o S&P 500"
type: spec
domain: clyde
regime: n/a
tags: [spec, clyde, backtest, calibracao, sweep, profit-factor, yfinance]
links_obrigatorios:
  parent_moc: "[[MOC_Clyde]]"
  vizinhos: "[[atom-profit-factor]] [[atom-rsi14]] [[atom-yfinance]] [[atom-ema50]]"
status: draft
ultima_revisao: 2026-05-20
---

# Calibração Quantitativa Offline — Sweep de Parâmetros sobre o S&P 500

> Laboratório de simulação histórica que varre os filtros técnicos do Clyde contra 2–4 anos de dados diários da totalidade do S&P 500, para descobrir o *sweet spot* de Profit Factor e Win Rate e diagnosticar a "paralisia por calibração".

Retorno: [[MOC_Clyde]] | [[MOC_FundScope]]

**Destinatário:** Claude Sonnet (executor)
**Autor:** Claude Opus 4.7 (Lead Quant Architect)
**Data:** 2026-05-20
**Escopo:** Novo pacote `bot/calibration/` — motor de research autónomo, vetorizado, com cache em disco. **Zero alterações ao caminho quente de produção.**

---

## 0. Contexto Operacional (LER ANTES DE TUDO)

### 0.1 O problema

O bot principal **não executa trades há 3 dias**. A hipótese de trabalho é **paralisia por calibração**: os filtros técnicos do sinal do Clyde estão demasiado apertados e, na conjuntura atual, nunca disparam um `BUY`.

O sinal vive em [bot/backtest.py:246-268](bot/backtest.py#L246-L268) (`_clyde_signal`) e replica o `_analyse_all` do `phase0.py`. Condição de `BUY`:

```
rsi <= RSI_BUY_MAX (35.0)        AND
ema50_above_200 is not False      AND
vol_ratio >= VOL_RATIO_MIN (0.8)  AND
regime not in {bear_correction, bear_capitulation}
```

A combinação **RSI ≤ 35 (sobrevenda) E EMA50 > EMA200 (tendência de alta)** é estruturalmente rara: um ativo em tendência de alta raramente cai a RSI 35. Estes dois filtros puxam em direções opostas. É o suspeito número um.

### 0.2 O objetivo

Construir um ambiente de **calibração rigorosa offline** que responda quantitativamente a: *"que combinação de parâmetros teria gerado mais lucro nos últimos 2–4 anos, sobre todo o S&P 500?"* — devolvendo um ranking por Profit Factor e Win Rate, com os parâmetros de produção atuais marcados na tabela para comparação direta.

### 0.3 Decisões de arquitetura já tomadas (input do utilizador)

1. **Motor autónomo flexível** — *não* reutilizar `_clyde_signal` diretamente. Implementar um motor vetorizado independente que replica as regras de produção como caso base, mas permite varrer features que a produção ainda não tem.
2. **EMA50: booleano E distância contínua, lado a lado** — manter o gate booleano `ema50 > ema200` como hoje, **e** adicionar a distância percentual contínua ao EMA50 como filtro varrível, reportando os dois para decidir se a feature nova acrescenta edge.

---

## 1. PAREDE DE FOGO — LIVE vs HISTÓRICO (requisito inegociável)

> O sistema de Live Trading é **intocável**. A integração yfinance desta spec é **estritamente** para simulação histórica offline.

### 1.1 Ficheiros PROIBIDOS de tocar

| Ficheiro | Papel | Estado exigido |
|---|---|---|
| [bot/price_feed.py](bot/price_feed.py) | Feed de preço em tempo real (Finnhub/T212) | **0 diffs** |
| [bot/phase0.py](bot/phase0.py) | Pipeline de execução do ciclo Live | **0 diffs** |
| [bot/api_client.py](bot/api_client.py) | Cliente Finnhub/T212 (rede em produção) | **0 diffs** |
| [bot/execution.py](bot/execution.py), [bot/position_ledger.py](bot/position_ledger.py), [bot/main.py](bot/main.py) | Caminho quente de ordens/estado | **0 diffs** |
| `bot/backtest.py`, `bot/mass_backtest.py` | Pipeline de observações da Bonnie | **0 diffs** (ver §1.3) |

### 1.2 Regra de isolamento

- Todo o código novo vive sob um **novo pacote** `bot/calibration/`. Não importa de `price_feed`, `phase0`, `api_client`, `execution`.
- A única dependência externa de dados é o **yfinance**. **Nunca** chamar o Finnhub: o *free tier* estoira com 500 ações (limite de 60 req/min). O download em lote diário é exclusivamente yfinance → cache em disco.
- O motor é **read-only** quanto a dados de produção. Escreve apenas em `data/cache/` e `data/calibration/` (ambos no `.gitignore`).

### 1.3 Porquê um pacote novo e não estender `mass_backtest.py`

`mass_backtest.py` tem um **propósito diferente**: gera `bonnie_observations.json` para alimentar o ML da Bonnie (uma observação por sinal BUY, com outcome). Misturar o sweep de calibração nesse ficheiro acoplaria dois domínios e arriscaria poluir o diário da Bonnie. A separação de responsabilidades mantém ambos limpos:

- `mass_backtest.py` → **gera dados de treino** para a Bonnie (mantém-se como está).
- `bot/calibration/` → **descobre parâmetros** para o Clyde (novo, isolado).

Ambos partilham *conceitos* (regime, indicadores, outcome), mas o motor de calibração reimplementa-os de forma vetorizada — ver §3.

---

## 2. Arquitetura do Pacote

```
bot/calibration/
  __init__.py
  __main__.py        # CLI: python -m bot.calibration ...
  universe.py        # constituintes do S&P 500 + snapshot em cache
  cache.py           # download yfinance em lote → parquet em disco
  indicators.py      # RSI14, EMA50/200, vol_ratio, distância EMA50 (vetorizado)
  regime.py          # série de regime SPY/RSP (vetorizado, zero look-ahead)
  candidates.py      # tabela mestra: 1 linha por (ticker, dia) + outcomes futuros
  sweep.py           # grelha de parâmetros + agregação de métricas
  report.py          # escreve CSV + REPORT.md
  metrics.py         # Profit Factor, Win Rate, expectancy, etc.

data/cache/ohlcv/    # <TICKER>.parquet  (gitignored)
data/cache/_meta.json
data/calibration/    # sweep_results.csv + REPORT.md  (gitignored)
```

### 2.1 Princípio de performance central

> **Calcula tudo uma vez; varre muitas vezes a barato.**

O motor atual ([backtest.py:335](bot/backtest.py#L335)) recalcula RSI/EMA **do zero para cada (ticker, data)**, refatiando o histórico completo a cada dia — uma armadilha O(N²): 500 tickers × ~1000 dias × recomputação de série inteira por dia. Para o S&P 500 isto é inviável.

A inversão: para cada ticker computa-se a **série inteira** de cada indicador **uma só vez** (vetorizado, Pandas/NumPy), e os outcomes futuros **uma só vez** por horizonte. Constrói-se assim uma **tabela mestra de candidatos** (§5). Cada combinação de parâmetros do sweep passa a ser apenas uma **máscara booleana + `groupby`** sobre essa tabela já pronta — milissegundos por combinação, não minutos.

---

## 3. Etapa 1 — Universo (S&P 500)

`universe.py`:

- `get_sp500_tickers(refresh: bool = False) -> list[str]`
  - Fonte primária: tabela da Wikipédia `https://en.wikipedia.org/wiki/List_of_S%26P_500_companies` via `pandas.read_html` (requer `lxml`).
  - Normalizar tickers para o formato yfinance: `BRK.B → BRK-B`, `BF.B → BF-B` (substituir `.` por `-`).
  - Persistir snapshot em `data/cache/sp500_constituents.json` com `{"as_of": "<ISO>", "tickers": [...]}`. Sem `--refresh`, lê o snapshot se existir.

> **⚠️ Viés de sobrevivência (limitação assumida, declarar no REPORT):** usar a composição *atual* do índice introduz *survivorship bias* — empresas removidas (falências, quedas) não entram no histórico, inflando artificialmente os retornos. Para a fase 1 (MVP) aceitamos este viés e documentamo-lo explicitamente no relatório. A correção (snapshots históricos de membership) fica para a fase 2.

---

## 4. Etapa 2 — Aquisição & Cache de Dados (guardrail de rate-limit)

`cache.py`:

- `ensure_ohlcv_cache(tickers, start, end, refresh=False) -> None`
  - Download em **lotes** via `yf.download(batch, start=, end=, interval="1d", auto_adjust=True, group_by="ticker", threads=True, progress=False)`.
  - **Tamanho de lote:** 50–100 tickers por chamada. Pausa de ~1–2 s entre lotes (cortesia de rate-limit; yfinance não é Finnhub mas pode ser estrangulado).
  - **Formato de cache:** **um Parquet por ticker** em `data/cache/ohlcv/<TICKER>.parquet` (colunar, tipado, ~10× mais leve e rápido que JSON). Requer `pyarrow`.
  - **Idempotência:** se `<TICKER>.parquet` existir e cobrir `[start, end]`, **não** re-descarregar (a menos de `refresh=True`). Registar cobertura em `data/cache/_meta.json` (`{ticker: {first, last, fetched_at}}`).
  - **Tolerância a falhas:** ticker que falhe/devolva vazio é registado e ignorado (semântica de sucesso parcial); o sweep corre com os que houver.
  - Descarregar também **SPY** e **RSP** (benchmark de regime) para a cache.

- `load_ohlcv(ticker) -> pd.DataFrame` — lê o Parquet, index `DatetimeIndex` (tz-naive, normalizado a meia-noite), colunas `[open, high, low, close, volume]`.

> **Janela de download:** para um sweep com lookback de 4 anos e EMA-200, descarregar ~**4 anos + 300 dias de calço** (`start - 300d`) para que a EMA-200 e o regime estejam estáveis no primeiro dia avaliável (mesma lógica do calço de 560 dias em [mass_backtest.py:138](bot/mass_backtest.py#L138)).

---

## 5. Etapa 3 — Indicadores Vetorizados & Tabela Mestra

### 5.1 Indicadores (`indicators.py`)

Todos sobre a série completa de cada ticker, **uma passagem**:

| Feature | Fórmula vetorizada | Paridade com produção |
|---|---|---|
| `rsi_14` | RSI de Wilder via `ewm(alpha=1/14, adjust=False)` sobre ganhos/perdas | Tem de bater [data_layer.py:171](bot/data_layer.py#L171) `compute_rsi` (teste de paridade §8) |
| `ema50`, `ema200` | `close.ewm(span=N, adjust=False).mean()` | Atenção: a produção ([data_layer.py:193](bot/data_layer.py#L193)) **semeia com SMA dos primeiros N** e só depois aplica recursão. Replicar essa semente (SMA seed + recursão) para paridade exata, **não** o `ewm` puro do Pandas. |
| `ema50_above_200` | `ema50 > ema200` (série booleana) | Gate booleano de produção |
| `ema50_dist_pct` | `(close - ema50) / ema50 * 100` | **Feature nova** (contínua) |
| `vol_ratio` | `volume / volume.rolling(20).mean()` | [backtest.py:367-368](bot/backtest.py#L367-L368) |

Barras com histórico insuficiente (`< MIN_HISTORY_BARS = 210`) ficam `NaN` e são excluídas a jusante.

### 5.2 Regime (`regime.py`)

Replicar a lógica de [backtest.py:176-239](bot/backtest.py#L176-L239) (`prime_regime_cache`) mas como **série diária** SPY/RSP, vetorizada, **zero look-ahead** (cada dia usa apenas dados ≤ esse dia):

- `pct_from_ema200 = (spy_close - ema200(spy)) / ema200 * 100`
- `ret_20d = spy_close.pct_change(20)`
- `breadth_healthy = (rsp/spy).pct_change(20) >= -0.02`
- Classificação idêntica: `bear_capitulation` / `bear_correction` / `bull_lateral` / `bull_trending` / `unknown`.

Devolve uma `Series` indexada por data → `regime`, que se faz *join* a cada ticker pela data.

### 5.3 Tabela mestra de candidatos (`candidates.py`)

`build_candidate_table(tickers, start, end, horizons) -> pd.DataFrame`

Uma linha por **(ticker, dia avaliável)** com **features no dia de entrada** + **outcomes futuros pré-computados** para cada horizonte. Colunas:

```
ticker, date, close,
rsi_14, ema50_above_200, ema50_dist_pct, vol_ratio, regime,
# por horizonte H em horizons (ex: 5, 10, 15):
out_H_final_return_pct, out_H_max_profit_pct, out_H_max_drawdown_pct, out_H_success
```

**Outcomes vetorizados, zero look-ahead** (definições idênticas a [backtest.py:275-290](bot/backtest.py#L275-L290) `_evaluate_outcome`, para comparabilidade):

- `final_return_pct = (close.shift(-H) - close) / close * 100`
- `max_profit_pct  = (high.rolling(H).max().shift(-H) - close) / close * 100` (janela **futura** de H barras)
- `max_drawdown_pct = (low.rolling(H).min().shift(-H) - close) / close * 100`
- `success = close.shift(-H) > close`

> **Look-ahead:** as features (RSI/EMA/vol/regime) usam dados **até e inclusive** o dia da linha; os outcomes usam **estritamente** as H barras seguintes. As últimas H barras de cada ticker ficam sem outcome (`NaN`) e são excluídas. Esta separação é o invariante de correção do laboratório — qualquer mistura invalida tudo.

A tabela mestra é construída **uma vez** e (opcionalmente) cacheada em `data/cache/candidates.parquet`.

---

## 6. Etapa 4 — Sweep de Parâmetros (Análise de Sensibilidade)

`sweep.py`:

### 6.1 Grelha de parâmetros (default)

| Parâmetro | Valores varridos | Produção atual |
|---|---|---|
| `rsi_buy_max` | 30, 32, 34, 35, 36, 38, 40 | **35.0** |
| `vol_ratio_min` | 0.0, 0.8, 1.0, 1.2 | **0.8** |
| `require_ema50_above_200` | True, False | **True** |
| `ema50_dist_min_pct` | None, −3, −1, 0, +2 | None (feature nova) |
| `apply_regime_veto` | True, False | **True** |
| `horizon` | 5, 10, 15 | 10 |

A grelha é configurável via CLI/JSON. Default ≈ 7×4×2×5×2×3 = **1680 combinações** — barato porque cada uma é só máscara + agregação sobre a tabela mestra.

### 6.2 Avaliação de uma combinação

Para cada combinação, construir a máscara de entrada (`BUY`) sobre a tabela mestra:

```python
mask = (cand.rsi_14 <= p.rsi_buy_max)
if p.require_ema50_above_200:
    mask &= cand.ema50_above_200
if p.ema50_dist_min_pct is not None:
    mask &= (cand.ema50_dist_pct >= p.ema50_dist_min_pct)
mask &= (cand.vol_ratio >= p.vol_ratio_min) | cand.vol_ratio.isna()  # vol em falta não veta
if p.apply_regime_veto:
    mask &= ~cand.regime.isin(["bear_correction", "bear_capitulation"])
trades = cand.loc[mask, f"out_{p.horizon}_final_return_pct"].dropna()
```

> O tratamento de `vol_ratio` em falta espelha [backtest.py:258](bot/backtest.py#L258), onde `vol_ratio is None` **não** bloqueia o BUY.

### 6.3 Métricas por combinação (`metrics.py`)

Sobre o conjunto de `final_return_pct` dos trades selecionados:

| Métrica | Definição |
|---|---|
| `n_trades` | nº de sinais BUY (linhas na máscara com outcome) |
| `win_rate` | `(returns > 0).mean()` — ver [[atom-rsi14]] / [[MOC_CRO]] |
| `profit_factor` | `Σ(returns>0) / |Σ(returns<0)|` — **> 1.5 = robusto** ([[atom-profit-factor]]) |
| `expectancy_pct` | `returns.mean()` (retorno médio por trade) |
| `median_return_pct` | `returns.median()` |
| `avg_max_drawdown_pct` | média de `max_drawdown_pct` dos trades |
| `total_return_pct` | `returns.sum()` (proxy de lucro acumulado, sem composição) |

**Guardas estatísticas:** combinações com `n_trades < N_MIN` (default **30**) são marcadas `low_sample=True` e **não** podem encabeçar o ranking — evita o "vencedor" com 3 trades sortudos. `profit_factor = inf` quando não há perdas (assinalar, não rankear como melhor).

---

## 7. Etapa 5 — Relatório (`report.py`)

Escrever dois artefactos em `data/calibration/`:

1. **`sweep_results.csv`** — grelha completa, uma linha por combinação, todas as métricas. Ordenável/inspecionável externamente.
2. **`REPORT.md`** — sumário legível:
   - Cabeçalho: universo (nº tickers, `as_of`), janela temporal, nº combinações, **aviso de survivorship bias** (§3).
   - **Linha da produção atual destacada** (RSI≤35, vol≥0.8, EMA50>200, veto de regime, H=10) com as suas métricas — quantos trades teria gerado, PF, WR. Isto responde diretamente à hipótese da "paralisia": se `n_trades` da produção for ~0, confirma-se o diagnóstico.
   - **Top-15 combinações** por Profit Factor (com `n_trades ≥ N_MIN`), tabela markdown.
   - **Top-15 por Win Rate** e **por expectancy**, para triangular (PF alto com WR baixo ⇒ poucos trades grandes; cuidado com robustez).
   - **Secção "Booleano vs Distância EMA50":** comparar as melhores combinações com `require_ema50_above_200=True` vs as que usam `ema50_dist_min_pct` contínuo — quantificar se a feature nova acrescenta edge real.
   - **Recomendação:** combinação sugerida + justificação (equilíbrio PF/WR/n_trades/drawdown), e o *delta* face à produção atual. **Não** alterar produção — apenas recomendar.

---

## 8. Plano de Validação (obrigatório antes de fechar)

1. **Paridade de indicadores:** para 5 tickers e 20 datas aleatórias, o `rsi_14` e `ema50/ema200` vetorizados batem o `compute_rsi`/`compute_ema` escalares de [data_layer.py](bot/data_layer.py) dentro de `1e-6`. Teste em `tests/`.
2. **Sanidade de look-ahead:** confirmar que `out_H_*` da última barra de cada ticker é `NaN` (não há futuro) e que nenhuma feature usa `shift(-k)` com `k>0`.
3. **Reprodução da produção:** a combinação (RSI≤35, vol≥0.8, EMA50>200, veto regime, H=10) corrida pelo motor novo deve produzir um conjunto de BUYs coerente com o que o `mass_backtest` geraria para os mesmos tickers/datas (spot-check de 1 ticker).
4. **Parede de fogo (crítico):**
   ```bash
   git status --porcelain bot/price_feed.py bot/phase0.py bot/api_client.py \
       bot/execution.py bot/main.py bot/backtest.py bot/mass_backtest.py
   ```
   Saída esperada: **vazia** (0 ficheiros de produção modificados).
   ```bash
   grep -rE "price_feed|phase0|api_client" bot/calibration/   # esperado: 0 hits
   ```
5. **Validação de sintaxe** (convenção do projeto):
   ```bash
   python -c "import ast,glob; [ast.parse(open(f).read()) for f in glob.glob('bot/calibration/*.py')]"
   ```
6. **Smoke run** com universo reduzido:
   ```bash
   python -m bot.calibration --start 2024-01-01 --end 2026-01-01 --limit 20 --horizons 10
   ```
   Deve produzir `REPORT.md` sem erros e com a linha da produção preenchida.

---

## 9. CLI

```
python -m bot.calibration \
    --start 2022-01-01 --end 2026-01-01 \
    --horizons 5,10,15 \
    [--refresh-cache] [--limit N] [--grid path/to/grid.json] [--n-min 30]
```

| Flag | Default | Função |
|---|---|---|
| `--start` / `--end` | end=hoje−15d, start=end−4a | Janela de simulação |
| `--horizons` | `10` | Horizontes de avaliação (dias de trading) |
| `--refresh-cache` | off | Força re-download yfinance |
| `--limit N` | sem limite | Usa só os primeiros N tickers (smoke test) |
| `--grid` | grelha §6.1 | JSON com grelha de parâmetros customizada |
| `--n-min` | 30 | Mínimo de trades para entrar no ranking |

---

## 10. Dependências

Adicionar a `requirements.txt` (confirmar se já presentes): `pandas`, `numpy`, `pyarrow` (Parquet), `lxml` (Wikipédia), `yfinance` (já existe). **Não** adicionar nada que o caminho de produção não tenha já — manter o footprint mínimo.

---

## 11. Fora de Escopo (Fase 1 / MVP)

Entrega faseada — fase 1 **determinística**, sem custos nem ML:

- ❌ **Custos de transação / slippage / spread** — fase 2. (Nota: a fase 1 usa retorno close-to-close puro; PF reais serão menores.)
- ❌ **Exit logic path-dependent** (take_profit 10% / stop_loss 5% do `RISK_CONFIG`) — a ordem intrabar de máximo/mínimo é desconhecida com barras diárias; modelar como aproximação só na fase 2.
- ❌ **Correção de survivorship bias** (membership histórico) — fase 2.
- ❌ **Walk-forward / out-of-sample split** — fase 2 (a fase 1 é in-sample; o sweep in-sample é exploratório, não confirmatório).
- ❌ **Qualquer escrita em produção** — o motor nunca toca `data/beta/`, `bonnie_observations.json`, nem o caminho Live.

---

## 12. Ordem de Execução (TL;DR para o executor)

1. `universe.py` → tickers S&P 500 + snapshot.
2. `cache.py` → download em lote + Parquet (idempotente). Validar com `--limit 20`.
3. `indicators.py` + teste de paridade (§8.1) **antes** de avançar — é o alicerce de correção.
4. `regime.py` → série de regime vetorizada.
5. `candidates.py` → tabela mestra (features + outcomes), com guarda anti-look-ahead.
6. `metrics.py` + `sweep.py` → grelha + agregação.
7. `report.py` → CSV + REPORT.md.
8. `__main__.py` / CLI.
9. Correr o plano de validação §8 completo. Parede de fogo (§8.4) é *gating*.

---

## Ligações

- [[atom-profit-factor]] — métrica primária de ranking do sweep (PF > 1.5 = robusto)
- [[atom-rsi14]] — feature central a calibrar (RSI_BUY_MAX 30–40)
- [[atom-ema50]] — gate booleano vs distância contínua (decisão "ambos")
- [[atom-yfinance]] — única fonte de dados; download em lote + cache Parquet
- [[MOC_Clyde]] — domínio do sinal a calibrar (`_clyde_signal`)
- [[MOC_CRO]] — consome PF/WR para monitorizar o edge do sistema
- Retorno: [[MOC_Clyde]] | [[MOC_FundScope]]
