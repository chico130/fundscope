---
id: moc-cro
title: "MOC — CRO (Chief Risk Officer Sistémico)"
type: moc
domain: cro
regime: n/a
tags: [moc, cro, risco-sistemico, kill-switch, regime, insights]
links_obrigatorios:
  parent_moc: "[[MOC_FundScope]]"
  vizinhos: "[[MOC_Bonnie]] [[MOC_Clyde]] [[MOC_Infraestrutura]]"
status: stable
ultima_revisao: 2026-05-19
---

# MOC — CRO (Chief Risk Officer Sistémico)

> O CRO é o árbitro final do sistema: observa, interpreta e pode travar toda a atividade de trading via kill-switch.

Hub: [[MOC_FundScope]] → este MOC → módulos de risco sistémico.

Spec completa: [[CRO_SPEC]]

---

## Módulos Principais

| Ficheiro | Responsabilidade |
|---|---|
| [[cro.py]] | Chief Risk Officer — risco sistémico, insights cognitivos, kill-switch |
| [[regime_detector.py]] | Classificação do regime de mercado macro (bull/bear/neutral) |
| [[notifier.py]] | Envia alertas e insights do CRO via Telegram |

---

## 3 Fases de Evolução do CRO

```
Fase 0 (Shadow Mode — ACTIVE)
  observe() → lê beta_trades, bonnie_log, portfolio, beta_analysis
  interpret() → analisa padrões sem intervir
  speak() → gera insights via Telegram (notifier.py)

Fase 1 (Dynamic Risk — PLANNED)
  compute_dynamic_risk_pct() → ajusta tamanho de posição por regime
  Integra equity curve feedback no sizing

Fase 2 (Kill-Switch — PLANNED)
  check_kill_switch() → activa bloqueio total se drawdown > threshold
  Bloqueia config_risco.json → permite_comprar = false
```

---

## Função observe() — Inputs (Hyperedge EXTRACTED)

O CRO.observe() lê directamente:
- `data/beta/beta_trades.json` — histórico de trades live
- `logs/bonnie_log.json` — snapshot de estado da [[MOC_Bonnie]]
- `data/beta/cro_insights.json` — insights anteriores (memória)
- `portfolio.json` — estado do portfolio ALFA

---

## Regime Detector

| Regime | Condição | Impacto em Clyde |
|---|---|---|
| BULL | SPY EMA-50 > EMA-200 + VIX < 20 | Sinais activos |
| BEAR | SPY EMA-50 < EMA-200 ou VIX > 30 | Sinais bloqueados |
| NEUTRAL | Zona intermédia | Apenas trades defensivos |

---

## Kill-Switch (Fase 2)

```
Trigger: drawdown_diario > max_daily_loss_pct (3%)
         OU drawdown_semanal > 8%
         OU n_erros_consecutivos > 3

Ação: config_risco.json → { "permite_comprar": false }
      Telegram alert via notifier.py
      Log em cro_insights.json
```

---

## Estado Emocional (config_risco.json)

O CRO mantém `estado_emocional` no config_risco.json:
- `normal` — operação padrão
- `cauteloso` — redução de tamanho de posição
- `parado` — kill-switch activo

---

## Ligações Cruzadas

- [[MOC_Bonnie]] — CRO lê bonnie_log; Bonnie respeita o kill-switch
- [[MOC_Clyde]] — regime do CRO condiciona sinais do Clyde
- [[MOC_Infraestrutura]] — notifier.py, config.py são dependências directas
- [[CRO_SPEC]] — especificação detalhada das 3 fases
- [[FASE-1]] — roadmap: CRO equity curve feedback
