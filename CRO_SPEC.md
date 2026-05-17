# 🏛️ SPEC: CHIEF RISK OFFICER (CRO) — SYSTEMIC RISK ENGINE

O CRO é o cérebro institucional supremo do FundScope.  
Não opera mercados, não procura lucros e não tem ego.  
A sua única missão é:

- Monitorizar o risco sistémico do ecossistema
- Policiar a eficiência da Bonnie e do Clyde
- Gerar narrativas analíticas autónomas (“dicas”) em Fase 0
- Atuar como disjuntor final de segurança da banca em Fase 2

O CRO não compra nem vende. Apenas decide **como e quando** o sistema pode arriscar e como deve aprender com vitórias e derrotas.

---

## 🧭 HIERARQUIA INSTITUCIONAL

```text
      MERCADO GERAL (Macro)
                │
        regime_detector.py
                │
          CRO — Chief Risk Officer 👑   [Aprova o ESTADO do sistema]
              ╱           ╲
    Bonnie — Filtro        Clyde — Sinais    [Aprovam / geram trades individuais]
              ╲           ╱
             execution.py   →   api_client (Trading 212)
```

- O **Clyde** encontra oportunidades.
- A **Bonnie** filtra e aprova entradas individuais.
- O **CRO** observa tudo de cima, valida a saúde do sistema, ajusta a agressividade do risco e pode suspender novas entradas em caso de perigo.

---

## 📊 INPUTS E OUTPUTS DO CRO

### Inputs (lidos de ficheiros existentes):

- `data/beta/beta_trades.json`  
  Histórico de trades (entradas/saídas, P&L, contexto técnico).

- `logs/bonnie_log.json`  
  Decisões da Bonnie (bloqueios, motivos, regras aplicadas).

- `data/beta/beta_analysis.json`  
  Snapshot da Fase 0 com regime, sinais, estatísticas de risco.

- `portfolio.json`  
  Estado atual da carteira (posições, equity total, cash livre).

- `regime` (via `regime_detector.py` ou ficheiro auxiliar)  
  Estado atual do mercado (`bull_trending`, `bull_lateral`, `bear_correction`, `bear_capitulation`, etc.).

### Outputs:

1. `data/beta/cro_insights.json`  
   Lista de “insights cognitivos” do CRO (texto + dados estruturados).

2. (Fase 1+) Sinais internos de risco dinâmico  
   Parâmetros calculados como `risk_per_trade_pct` e indicadores de “forma” da dupla Bonnie/Clyde.

3. (Fase 2) Escrita em `config_risco.json`  
   Kill-Switch institucional: `permite_comprar: false` quando risco sistémico é demasiado elevado.

---

## 📈 FASE 0 — NARRATIVE ENGINE & SHADOW LEARNING

Nesta fase, o CRO opera em modo **Shadow**.  
Não bloqueia ordens, não altera parâmetros de risco. Limita-se a:

- Observar tudo o que acontece
- Tirar conclusões
- Escrever essas conclusões em JSON + texto
- Mandar “dicas” para o Telegram via notifier

### 🧠 Funções principais do CRO (Fase 0)

1. `observe()`

   - Lê:
     - `data/beta/beta_trades.json`
     - `logs/bonnie_log.json`
     - `data/beta/beta_analysis.json`
     - `portfolio.json`
   - Extrai:
     - Últimos trades fechados
     - Sequências de vitórias/derrotas
     - Regime de mercado atual
     - Exposição por setor / número de posições
     - Bloqueios recentes da Bonnie

2. `interpret()`

   - Analisa o que observou e constrói insights como:
     - “3 perdas consecutivas em ações Tech com RSI de entrada > 60 em regime `bear_correction`”
     - “2 vitórias fortes em `bull_trending` com volume > 1.5× a média”
   - Liga estes padrões a conclusões do tipo:
     - “RSI de entrada atual é demasiado alto neste regime”
     - “A Bonnie está a ser demasiado conservadora / demasiado permissiva”
     - “Exposição por setor está a aproximar-se de um nível perigoso”

3. `speak()`

   - Escreve cada conclusão em `data/beta/cro_insights.json` com este formato:

```json
{
  "timestamp": "2026-05-17T02:45:00Z",
  "tipo": "insight_cognitivo",
  "de": "CRO",
  "resumo": "Entrada em AMD falhou em bear_correction com RSI alto.",
  "mensagem": "AMD fechou com perda de -12€. O Clyde comprou com RSI de entrada a 62, enquanto o regime atual era 'bear_correction'. Conclusão: entradas com RSI > 60 em correção de mercado são armadilhas frequentes neste sistema.",
  "sugestao": "Reduzir o teto de RSI de entrada para 55 enquanto o S&P 500 estiver abaixo da EMA-200.",
  "parametro_alvo": "rsi_entry_ceiling",
  "valor_atual": 60,
  "valor_proposto": 55,
  "fator_confianca": "alta",
  "aprovado_por_humano": false
}
```

   - Opcional: envia um resumo destes insights para o Telegram através de `bot/notifier.py`, como parte do relatório diário/por ciclo.

### Integração técnica Fase 0

- Novo ficheiro: `bot/cro.py` com funções/classe:
  - `run_cro_cycle()` → chama `observe()`, `interpret()`, `speak()`.
- Chamado no final da Fase 0:
  - No `phase0.py`, depois de gerar `beta_analysis.json`, chamar `run_cro_cycle()`.
- Nada é alterado em:
  - `execution.py` (continua igual)
  - `config_risco.json` (apenas leitura pela Bonnie nesta fase)
  - `LIVE_TRADING` (continua `False` até decisão explícita).

---

## ⚙️ FASE 1 — CONTEXTUAL POSITION SIZING (MOTOR DE RISCO DINÂMICO)

Na Fase 1, o CRO começa a influenciar **como** o capital é usado por trade, mas ainda não tem Kill-Switch. Aqui nasce o “percentual dinâmico” em vez de um fixo 1% da banca.

### Fórmula base (conceito)

```text
Risco_Dinamico = Base_Risco_Pct × Fator_Regime × Fator_Performance × Fator_Volatilidade
```

- `Base_Risco_Pct`: ex. 1.0% da equity total (configurável)
- `Fator_Regime`:
  - `bull_trending` → 1.0
  - `bull_lateral` → 0.8
  - `bear_correction` / `bear_capitulation` → 0.5
- `Fator_Performance` (Bonnie+Clyde últimos N trades reais):
  - Win rate > 60% → 1.2
  - Win rate entre 45–60% → 1.0
  - Win rate < 45% → 0.7
- `Fator_Volatilidade` (ex. ATR / média ATR):
  - Volatilidade normal → 1.0
  - Volatilidade extrema → 0.8

Exemplo:

- Base = 1.0%
- Regime = `bear_correction` → × 0.5
- Performance recente fraca → × 0.7
- Volatilidade alta → × 0.8

→ `Risco_Dinamico ≈ 1.0% × 0.5 × 0.7 × 0.8 = 0.28% da equity por trade`

Este valor é passado para o `execution.py` como “budget de risco” máximo daquele trade.  
A partir daqui, o position sizing é calculado assim:

```text
Risco_Euro_Por_Trade = Risco_Dinamico × Equity_Total
Risco_Por_Acao       = |Preço_Entrada - Preço_Stop|
Qtd_Acoes            = floor(Risco_Euro_Por_Trade / Risco_Por_Acao)
```

- **Reinvestir** aqui significa: a ação é vendida; o capital volta para `Free Cash` na Trading 212; o CRO volta a calculá-lo como parte da equity total para o próximo trade. Não há transferência externa de dinheiro, apenas rotação interna.

### Integração técnica Fase 1

- O `CRO` passa a expor uma função, p.ex. `compute_dynamic_risk_pct()` que devolve o `Risco_Dinamico`.
- O `execution.py` passa a usar essa função em vez de um fator fixo:
  - Sai o `tamanho_maximo_posicao` como freio principal.
  - Entra o cálculo de `Risco_Euro_Por_Trade` baseado no output do CRO e no stop proposto pelo Clyde/Bonnie.
- O `learner.py` alimenta o CRO com estatísticas reais de performance (últimos N trades) para cálculo de `Fator_Performance`.

---

## 📌 OBSERVAÇÃO: Risco Cambial EUR/USD

> **Contexto:** A conta demo (e futura conta real) está denominada em EUR. A watchlist e a execução operam maioritariamente em acções US cotadas em USD. A conversão EUR/USD é feita automaticamente pela T212 e pelo bot (via `yfinance EURUSD=X`).

**Recomendação para fase futura:** Implementar monitorização do risco cambial como métrica autónoma do CRO, incluindo:

1. **Tracking do câmbio na entrada:** registar o `eurusd` no momento de cada trade BUY em `beta_trades.json` para calcular o impacto cambial no P&L final.
2. **Alerta de desvio cambial:** se o EUR/USD se mover mais de ±3% desde a entrada numa posição aberta, o CRO deve emitir um insight de alerta no `cro_insights.json`.
3. **ETFs hedged como alternativa (Fase 2+):** considerar incluir na watchlist ETFs como CSPX (S&P 500 em EUR, LSE) para posições de maior duração onde o risco cambial se acumula.

*Esta nota não implica mudança imediata — os retornos históricos do S&P 500 em EUR continuam positivos a longo prazo. A prioridade é acumular dados reais de performance antes de adicionar camadas de hedging.*

---

## 🛡️ FASE 2 — DISJUNTOR INSTITUCIONAL (KILL-SWITCH)

Na Fase 2, o CRO ganha autoridade máxima de bloqueio.  
Ainda não muda parâmetros de forma silenciosa, mas **pode suspender novas entradas** escrevendo em `config_risco.json`.

### Gatilhos sugeridos para o Kill-Switch

1. **Drawdown diário excessivo**

   - Se a perda combinada (realizada + não realizada) das posições num único dia exceder X% da banca total:
     - CRO ativa `permite_comprar: false` em `config_risco.json`.
     - Escreve um insight de nível `emergency` em `cro_insights.json`.
     - Notifica via Telegram:
       - "🏛️ [CRO • EMERGENCY KILL-SWITCH] 🚨 Trading suspenso. Drawdown diário superior a X%. Novas entradas bloqueadas para proteger capital."

2. **Crash de confiança**

   - Se houver N perdas consecutivas atingindo stop loss máximo (por exemplo 3) num intervalo curto:
     - CRO assume mudança súbita de regime ou problema estrutural.
     - Ativa `permite_comprar: false` durante um período (ex. 48 horas).
     - Regista esta suspensão com timestamps de início e fim previstos.

3. **Exposição setorial extrema**

   - Se a exposição a um setor específico exceder `RISK_CONFIG["max_sector_pct"]` por trade repetidamente:
     - CRO força pausa temporária em novas entradas naquele setor.

### Requisito crítico: override manual

Mesmo com Kill-Switch, o **humano mantém sempre a última palavra**:

- O `config_risco.json` continua totalmente editável pelo utilizador.
- Qualquer bloqueio automático do CRO deve ser marcado com:
  - `"origem": "CRO"`
  - `"motivo": "drawdown_diario" | "perdas_consecutivas" | ...`
- O utilizador pode reverter o bloqueio manualmente, e o CRO deve registar isso como:

```json
{
  "timestamp": "...",
  "tipo": "override_manual",
  "de": "Humano",
  "mensagem": "Francisco reativou permite_comprar após suspensão automática do CRO.",
  "motivo_original": "drawdown_diario"
}
```

---

## 🔌 INTEGRAÇÃO COM O ECOSSISTEMA FUND SCOPE

### Ficheiro: `bot/cro.py`

Responsabilidades:

- Fase 0:
  - `run_cro_cycle()`:
    - `observe()`
    - `interpret()`
    - `speak()`
- Fase 1:
  - `compute_dynamic_risk_pct()`:
    - Devolve o percentual de risco sugerido para o próximo trade com base em regime, performance recente e volatilidade.
- Fase 2:
  - `check_kill_switch()`:
    - Verifica drawdown diário, perdas consecutivas e exposição setorial.
    - Se necessário, escreve em `config_risco.json` para bloquear compras novas.

### Ficheiros a ajustar (mais tarde, por fase)

- `bot/phase0.py`
  - No final do ciclo, chamar `run_cro_cycle()`.

- `bot/execution.py`
  - Fase 1: usar o output do CRO para calcular o tamanho da posição com base no risco por trade.
  - Fase 2: respeitar `permite_comprar` definido pelo CRO através de `config_risco.json` (já está parcialmente implementado).

- `bot/learner.py`
  - Fornecer ao CRO estatísticas consolidadas (win rate, P&L médio, etc.) para cálculo de `Fator_Performance`.

- `bot/notifier.py`
  - Incluir os insights mais recentes do CRO nas mensagens enviadas via Telegram.

---

## 🧾 PROMPT PARA O CLAUDE CODE (FASE 0 PRIMEIRO)

Depois de adicionares este ficheiro ao repositório como `CRO_SPEC.md`, podes dar esta instrução ao Claude Code:

```text
Claude, adicionei o ficheiro CRO_SPEC.md com a especificação do Chief Risk Officer (CRO) do FundScope.

Quero implementar apenas a FASE 0 (Narrative Engine & Shadow Learning) por agora. Por favor:

1. Cria o ficheiro bot/cro.py com:
   - Uma função principal run_cro_cycle().
   - Três funções internas: observe(), interpret(), speak(), seguindo a especificação do CRO_SPEC.md.
   - A leitura de:
     - data/beta/beta_trades.json
     - logs/bonnie_log.json
     - data/beta/beta_analysis.json (se existir)
     - portfolio.json (se existir)
   - A escrita de insights em data/beta/cro_insights.json no formato descrito na secção "FASE 0 — NARRATIVE ENGINE & SHADOW LEARNING".

2. Integra o CRO no final do ciclo da Fase 0:
   - Em bot/phase0.py, depois de gerar beta_analysis.json e fazer o git push, chama run_cro_cycle().

3. Adiciona uma integração simples com o notifier:
   - Se houver novos insights gerados neste ciclo, envia um resumo de 1–2 frases via bot/notifier.py para o Telegram.

4. Garante que:
   - Nada é alterado em execution.py nesta fase.
   - O CRO não mexe em config_risco.json ainda.
   - Em caso de erro ao ler ficheiros, o CRO falha em silêncio (log_error) e não interrompe o ciclo principal do bot.

Mostra-me o esqueleto de bot/cro.py antes de gravar as alterações.
```
