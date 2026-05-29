# REGRA DE OURO DO PROJETO: ZERO ERROS REPETIDOS

Sempre que o Claude alterar código neste repositório, deve ler este ficheiro PRIMEIRO
e garantir que não reintroduz nenhum dos erros abaixo.

---

## ERROS CONHECIDOS E RESOLVIDOS

### [2026-05-xx] Cache agressiva no iPhone 13 — stock.html e home não actualizam
**Sintoma:** Página mostra dados desactualizados. Home desactualizada (não mostra ganhos). stock.html consome dados estáticos de data/beta.
**Causa raiz:** Service Worker (sw.js) com estratégia cache-first. Ficheiros servidos da cache sem verificar se há versão mais recente no servidor.
**Solução aplicada:** Adicionar cache-busting com parâmetro `?v=${Date.now()}` em todos os fetch() dinâmicos. Mudar estratégia do sw.js para network-first (ou stale-while-revalidate) para ficheiros JSON de dados.
**Prevenção futura:** Nunca usar cache-first para ficheiros .json de dados. Sempre usar network-first ou adicionar `?v=` timestamp.
**Ficheiros afectados:** sw.js, stock.html, index.html (home), watchlist.html

### [2026-05-xx] Watchlist não mostra avaliação do CRO/Clyde
**Sintoma:** A coluna "Avaliação" aparece vazia ou desaparece da watchlist.
**Causa raiz:** stock.html e watchlist.html lêem dados de data/beta via fetch estático. O projecto define que o estado nunca é calculado no browser — os dados têm de vir pré-calculados dos scripts Python e estar actualizados no JSON.
**Solução aplicada:** [a preencher após fix]
**Prevenção futura:** Nunca calcular estado/avaliação no browser. Sempre pré-calcular nos scripts Python e servir via JSON.

### [2026-05-xx] Ordens BUY rejeitadas pela T212 (HPE_US_EQ a $42.94)
**Sintoma:** Clyde detecta sinal válido, Bonnie aprova, mas a ordem é rejeitada pela API T212. Mensagem Telegram: "Ordem BUY HPE_US_EQ rejeitada pela T212. Detalhe no log do GitHub Actions."
**Causa raiz:** [a diagnosticar — ver secção INVESTIGAÇÃO ACTIVA]
**Solução aplicada:** [pendente]
**Prevenção futura:** [pendente após diagnóstico]

---

## INVESTIGAÇÃO ACTIVA

### Ordens BUY rejeitadas pela T212
- Último caso: HPE_US_EQ, $42.94, RSI=83.0, vol=2.5×, força=75%, regime bull_trending
- Mensagem exacta recebida: "Ordem BUY HPE_US_EQ rejeitada pela T212. Detalhe no log do GitHub Actions."
- Ciclo seguinte tentou novamente? Sim (confirmado pela mensagem Telegram)
- Resultado do ciclo seguinte: desconhecido
- Próximo passo: ler o log do GitHub Actions do ciclo em que ocorreu e identificar o código de erro HTTP devolvido pela T212

---

## REGRAS DE EDIÇÃO OBRIGATÓRIAS

1. Antes de editar sw.js: confirmar que a estratégia de cache para ficheiros .json é network-first
2. Antes de editar qualquer HTML: confirmar que os fetch() de dados .json têm `?v=${Date.now()}`
3. Antes de editar bot/phase0.py ou bot/api_client.py: ler a secção "Ordens BUY rejeitadas" acima
4. Após resolver qualquer item em "INVESTIGAÇÃO ACTIVA": mover para "ERROS CONHECIDOS E RESOLVIDOS" com a solução completa
5. Após introduzir qualquer nova fix: adicionar entrada neste ficheiro com data, sintoma, causa e solução
