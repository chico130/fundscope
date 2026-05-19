---
id: spec-handoff
title: "Spec Handoff — Auth, Routing e serve.py"
type: spec
domain: infra
regime: n/a
tags: [spec, auth, serve, routing]
links_obrigatorios:
  parent_moc: "[[MOC_Infraestrutura]]"
  vizinhos: "[[MOC_Frontend]] [[MOC_CRO]]"
status: stable
ultima_revisao: 2026-05-19
---
# FundScope — Documento de Especificação Técnica e Plano de Acção

Retorno: [[MOC_Infraestrutura]]

**Destinatário:** Claude 3.5 Sonnet (executor)
**Autor:** Claude Opus 4.7 (planeador)
**Data:** 2026-05-19
**Escopo:** Duas alterações estruturais — (1) Normalização de Roteamento de Links e (2) Fusão Estrutural do Portfólio com Login.

---

## 0. Contexto Operacional (LER ANTES DE TUDO)

- **Host actual:** VPS `134.98.141.58` (HTTP porta 80, sem TLS) — substitui o deploy estático antigo em `https://chico130.github.io/fundscope/`.
- **Servidor:** `serve.py` (Python stdlib `http.server`, ver linha 192–204) já corre na porta 8080 localmente; em produção é proxiado para a porta 80. O servidor já tem **base de autenticação** parcial — sessões em memória, hash SHA-256, helpers `_new_token()`, `_valid_token()`, `_verify_credentials()`. **Estender, não reescrever.**
- **Frontend é estático** (HTML/JS puro, sem framework, sem bundler). Editar HTML directamente.
- **Bots:**
  - **[[MOC_Clyde|Clyde]]** (estratégia/execução) escreve em `data/beta/beta_summary.json`, `beta_positions.json`, `beta_trades.json`, `beta_analysis.json`.
  - **[[MOC_Bonnie|Bonnie]]** (sentimento/audit) escreve em `data/beta/beta_analysis.json` (campos `bonnie_*`), `cro_insights.json`.
  - **Trading 212 Live** (estado real da conta) escreve em `portfolio.json` (raiz do projecto, não em `data/beta/`).
- **Regra de ouro do projecto:** consultar [[[graphify-out/GRAPH_REPORT|GRAPH_REPORT.md]]](graphify-out/GRAPH_REPORT.md) antes de explorar; **não** abrir `bot/strategy.py`, `bot/cro.py`, `bot/bonnie.py` salvo necessidade explícita.

---

# REQUISITO 1 — Normalização de Roteamento de Links

## 1.1 Diagnóstico (mapa exacto de ocorrências)

Foi feita auditoria com `grep` ao padrão `https?://(chico130\.github\.io|134\.98\.141\.58|localhost|127\.0\.0\.1)`. Resultado:

| Ficheiro:Linha | URL hardcoded | Tipo | Acção |
|---|---|---|---|
| [earnings.html:142](earnings.html#L142) | `https://chico130.github.io/fundscope/search.html` | Logo `<a class="logo">` | Substituir por `/search.html` |
| [index.html:2](index.html#L2) | `https://chico130.github.io/fundscope/search.html` (meta-refresh + JS redirect) | Redirect root | Substituir por `/search.html` |
| [portfolio.html:336](portfolio.html#L336) | `https://chico130.github.io/fundscope/search.html` | Logo | `/search.html` |
| [portfolio.html:619](portfolio.html#L619) | `http://localhost:8080/api/login` | fetch JS | `/api/login` |
| [search.html:119](search.html#L119) | `https://chico130.github.io/fundscope/search.html` | Logo | `/search.html` |
| [search.html:227](search.html#L227) | `http://localhost:8080/api/save-watchlist` | fetch JS | `/api/save-watchlist` |
| [stock.html:167](stock.html#L167) | `https://chico130.github.io/fundscope/search.html` | Logo | `/search.html` |
| [watchlist.html:398](watchlist.html#L398) | `http://localhost:8080/api/save-watchlist` | fetch JS | `/api/save-watchlist` |

**Casos a NÃO tocar** (deliberadamente):
- [portfolio.html:576](portfolio.html#L576) — footer `https://github.com/chico130/fundscope` aponta ao repositório real no GitHub (correcto, mantém-se).
- [live_portfolio.html:15](live_portfolio.html#L15), `<link rel="preconnect" href="https://fonts.gstatic.com">` — CDN externo legítimo.
- `/fundscope/manifest.json` e `/fundscope/sw.js` em headers PWA — **possível bug separado** do [[MOC_Frontend|GitHub Pages]]; ver §1.4.

## 1.2 Regra Canónica

> **Todos os links internos (HTML ou JS-fetch) usam caminhos absolutos relativos à raiz, começando por `/`. Sem esquema (`http://`/`https://`), sem host, sem porta.**

Exemplos válidos: `/search.html`, `/api/login`, `/data/beta/beta_summary.json`.
Exemplos inválidos: `http://localhost:8080/api/login`, `https://chico130.github.io/...`, `./search.html` (a navegação cross-pasta partir-se-ia em sub-rotas).

## 1.3 Plano de Execução (passo a passo)

**Passo 1.1 — Auditoria final (validação antes de editar).** Correr este comando a partir da raiz do projecto:

```powershell
# PowerShell — listar todos os hits que devem desaparecer
Select-String -Path *.html,*.js -Pattern 'https?://(chico130\.github\.io|localhost:8080|127\.0\.0\.1:8080|134\.98\.141\.58)'
```

Resultado esperado **antes** das edições: 8 linhas (ver tabela §1.1). **Após** as edições: 0 linhas.

**Passo 1.2 — Substituições, ficheiro a ficheiro.** Para cada ficheiro abaixo, usa a ferramenta `Edit` com `old_string`/`new_string` mínimos mas únicos:

1. `earnings.html` linha 142 — `href="https://chico130.github.io/fundscope/search.html"` → `href="/search.html"`
2. `index.html` linha 2 — substituir o conteúdo inteiro do `<head>` para:
   ```html
   <meta charset="UTF-8"><meta http-equiv="refresh" content="0;url=/search.html"><script>window.location.replace('/search.html')</script>
   ```
3. `portfolio.html` linha 336 — `href="https://chico130.github.io/fundscope/search.html"` → `href="/search.html"`
4. `portfolio.html` linha 619 — `fetch('http://localhost:8080/api/login', {` → `fetch('/api/login', {`
5. `search.html` linha 119 — idêntico ao item 1
6. `search.html` linha 227 — `fetch('http://localhost:8080/api/save-watchlist',{` → `fetch('/api/save-watchlist',{`
7. `stock.html` linha 167 — idêntico ao item 1
8. `watchlist.html` linha 398 — idêntico ao item 6

**Passo 1.3 — Validação automática.**
```powershell
Select-String -Path *.html,*.js -Pattern 'https?://(chico130\.github\.io|localhost:8080|127\.0\.0\.1:8080|134\.98\.141\.58)'
```
Saída esperada: **vazia**. Caso contrário, repetir as substituições omitidas.

**Passo 1.4 — Validação manual no browser:**
1. `python serve.py 8080`
2. Abrir `http://localhost:8080/` → deve redirigir para `/search.html`.
3. Clicar no logo em cada página (`search`, `portfolio`, `stock`, `earnings`, `watchlist`, `markets`, `news`, `live_portfolio`) — todas devem voltar a `/search.html` sem fugir para github.io.
4. Login em `/portfolio.html` deve continuar a funcionar (network tab → `POST /api/login`, status 200 ou 401).
5. Salvar watchlist em `/search.html` e `/watchlist.html` deve devolver 200 (após login).

## 1.4 Nota separada — paths PWA

Há referências `/fundscope/manifest.json` e `/fundscope/sw.js` (legado do [[MOC_Frontend|GitHub Pages]], em que o site corria em `/fundscope/`). No VPS o site corre na raiz `/`. **Acção sugerida** (incluída nesta passagem):

| Ficheiro:Linha | Antigo | Novo |
|---|---|---|
| `live_portfolio.html:8` | `href="/fundscope/manifest.json"` | `href="/manifest.json"` |
| `portfolio.html:1302` (e equivalentes) | `register('/fundscope/sw.js')` | `register('/sw.js')` |

Procurar `/fundscope/` em todos os HTML antes de fechar a tarefa:
```powershell
Select-String -Path *.html,*.js -Pattern '/fundscope/'
```
Substituir todas as ocorrências por `/`.

---

# REQUISITO 2 — Fusão Estrutural e Segurança do Portfólio

## 2.1 Visão Geral da Arquitectura Alvo

```
┌──────────────────────────────────────────────────────────────────────┐
│ /portfolio.html   (única página de portfólio — Live + Análise)       │
│   • Bloqueada por login overlay até token válido                     │
│   • 3 separadores (tabs):                                            │
│       [Live]   ← KPIs Trading 212 + posições reais + polling 30s     │
│       [Clyde]  ← Backtest OOS + risk + trades simulados              │
│       [Bonnie] ← Análise de sentimento + CRO insights + watchlist    │
│   • Polling unificado para o tab activo (não polling em tabs ocultas)│
│   • Logout limpa token e volta ao overlay                            │
└──────────────────────────────────────────────────────────────────────┘
                              │
                              │ fetch (Authorization: Bearer <token>)
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│ serve.py                                                              │
│   • POST /api/login           → emite token (já existe)              │
│   • GET  /api/portfolio       → portfolio.json (NOVO, protegido)     │
│   • GET  /api/beta/<file>     → data/beta/*.json (NOVO, protegido)   │
│   • GET  /portfolio.html      → entregue só se token cookie válido†  │
│   • GET  /api/save-watchlist  → já existe, protegido                 │
│   † ver §2.3 para opção alternativa "client-gate"                    │
└──────────────────────────────────────────────────────────────────────┘
```

## 2.2 Decisões de Design Fixas

1. **`live_portfolio.html` é extinguido**, não apagado de imediato. Substitui-se o conteúdo do ficheiro por uma redirecção de uma linha para `/portfolio.html#live`, para não partir bookmarks antigos.
2. **A barreira de auth vive no backend (`serve.py`)**, não apenas no JS — caso contrário um atacante pede directamente `data/beta/beta_summary.json` e contorna o login. Os endpoints novos `/api/portfolio` e `/api/beta/<file>` exigem `Authorization: Bearer <token>`.
3. **Password mestra vem de `.env`**, não de `data/user_credentials.json` (este último mantém-se para retro-compat — `_verify_credentials` continua a funcionar como fallback). Variável: `FUNDSCOPE_AUTH_PASSWORD`. Username único: `FUNDSCOPE_AUTH_USER` (default `admin`).
4. **Token vive 7 dias** (já é o `SESSION_TTL` actual). Sem refresh tokens — basta re-login.
5. **Polling unificado:** UMA única função `tick()` no `/portfolio.html` que, conforme o tab activo, chama um subset de endpoints. Cadência: 30s (alinhar com o que já existe em `live_portfolio.html`).

## 2.3 Decisão Pendente — Como bloquear `/portfolio.html`?

Há duas abordagens; **recomendo A** pela simplicidade e por não exigir cookies:

- **(A) Client-gate (recomendado):** o HTML serve-se sem auth, mas o JS testa `localStorage.fs_token` no carregamento. Se ausente/inválido, mostra overlay de login. Os DADOS (JSONs) são protegidos no backend (§2.2 ponto 2). Isto significa que mesmo um utilizador anónimo recebe o HTML, mas não vê nada (todos os fetches devolvem 401).
- **(B) Server-gate:** `serve.py` intercepta `GET /portfolio.html` e exige cookie `fs_token`. Mais "limpo" mas obriga a passar o token via cookie em vez do header `Authorization`, o que diverge do padrão actual da app.

> **Decisão deste plano: A.** Se o utilizador exigir mais tarde server-gate, é uma evolução aditiva.

## 2.4 Alterações Detalhadas — `serve.py`

**Adicionar após a constante `USER_UNIVERSE_PATH` (≈ linha 31):**

```python
# Master credential (env-first, file-fallback)
AUTH_USER     = os.environ.get('FUNDSCOPE_AUTH_USER', 'admin')
AUTH_PASSWORD = os.environ.get('FUNDSCOPE_AUTH_PASSWORD', '')  # vazio = desactivado
```

**Carregar `.env` no topo (após imports):**
```python
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # opcional em produção; falla silently se python-dotenv não estiver instalado
```

**Modificar `_verify_credentials`** para aceitar primeiro a credencial do `.env`:

```python
def _verify_credentials(username: str, password: str) -> bool:
    # 1) Master credential vinda de .env
    if AUTH_PASSWORD and username == AUTH_USER and \
       secrets.compare_digest(password, AUTH_PASSWORD):
        return True
    # 2) Fallback — ficheiro de credenciais existente
    try:
        with open(CREDENTIALS_PATH, encoding='utf-8') as f:
            creds = json.load(f)
        stored = creds.get(username)
        if not stored:
            return False
        pw_hash = hashlib.sha256(password.encode('utf-8')).hexdigest()
        return secrets.compare_digest(stored, pw_hash)
    except Exception:
        return False
```

### Localização dos ficheiros JSON (IMPORTANTE para o executor)

Antes de implementar, notar que os ficheiros JSON do projecto estão em **dois níveis** distintos:

| Ficheiro | Localização real | Endpoint |
|---|---|---|
| `portfolio.json` | Raiz (`./portfolio.json`) | `/api/portfolio` (auth) |
| `markets.json` | Raiz (`./markets.json`) | `/api/data/markets.json` (auth) |
| `earnings.json` | Raiz (`./earnings.json`) | `/api/data/earnings.json` (auth) |
| `beta_*.json`, `cro_insights.json`, `regime.json`, etc. | `data/beta/` | `/api/beta/<file>` (auth) |

`markets.json` e `earnings.json` **não existem em `data/beta/`** — existem na raiz. Tentativas de os servir via `/api/beta/` devolveriam 403. Portanto é necessário um segundo endpoint: `/api/data/<file>`.

**Adicionar três rotas novas em `do_GET`:**

```python
def do_GET(self):
    parsed = urllib.parse.urlparse(self.path)
    if parsed.path == '/api/stock-review':
        self._handle_stock_review(parsed.query)
    elif parsed.path == '/api/portfolio':
        self._handle_get_portfolio()
    elif parsed.path.startswith('/api/beta/'):
        self._handle_get_beta(parsed.path[len('/api/beta/'):])
    elif parsed.path.startswith('/api/data/'):
        self._handle_get_data(parsed.path[len('/api/data/'):])
    else:
        super().do_GET()
```

**Implementar os handlers:**

```python
# Ficheiros em data/beta/ — estado interno dos bots (auth obrigatória)
ALLOWED_BETA = {
    'beta_summary.json',
    'beta_positions.json',
    'beta_trades.json',
    'beta_analysis.json',
    'beta_equity.json',
    'cro_insights.json',
    'regime.json',
    'watchlist.json',
    'position_meta.json',
    'positions_ledger.json',
    'earnings_ai.json',
    'watchlist_fundamentals.json',
}

# Ficheiros na raiz — dados de mercado (auth obrigatória porque alimentam a página protegida)
ALLOWED_DATA = {
    'markets.json',
    'earnings.json',
}

def _require_auth(self) -> bool:
    if _valid_token(self._get_bearer_token()):
        return True
    self._send_json({'error': 'não autenticado'}, 401)
    return False

def _handle_get_portfolio(self):
    if not self._require_auth():
        return
    try:
        with open('portfolio.json', 'r', encoding='utf-8') as f:
            self._send_json(json.load(f))
    except FileNotFoundError:
        self._send_json({'error': 'portfolio.json em falta'}, 404)
    except Exception as e:
        self._send_json({'error': str(e)}, 500)

def _handle_get_beta(self, filename: str):
    if not self._require_auth():
        return
    if filename not in ALLOWED_BETA:
        self._send_json({'error': 'recurso não permitido'}, 403)
        return
    try:
        with open(f'data/beta/{filename}', 'r', encoding='utf-8') as f:
            self._send_json(json.load(f))
    except FileNotFoundError:
        self._send_json({'error': 'em falta'}, 404)
    except Exception as e:
        self._send_json({'error': str(e)}, 500)

def _handle_get_data(self, filename: str):
    if not self._require_auth():
        return
    if filename not in ALLOWED_DATA:
        self._send_json({'error': 'recurso não permitido'}, 403)
        return
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            self._send_json(json.load(f))
    except FileNotFoundError:
        self._send_json({'error': 'em falta'}, 404)
    except Exception as e:
        self._send_json({'error': str(e)}, 500)
```

**Whitelists são críticas** — sem elas, `/api/beta/../../etc/passwd` seria explorável. **Nunca substituir por path-join sem validação prévia contra a whitelist.**

**Atualizar `.env`** (preservando o que já existe):
```
FUNDSCOPE_AUTH_USER=admin
FUNDSCOPE_AUTH_PASSWORD=<password forte definida pelo utilizador>
```

**Atualizar prompts iniciais (linhas 195–199 de `serve.py`):** remover a linha `Live Portfolio: ...live_portfolio.html` (já não existe como página separada).

## 2.5 Alterações Detalhadas — `portfolio.html`

A página actual já tem:
- Overlay de login (`loginOverlay`)
- Lógica de tabs `clyde`/`bonnie`/`about`
- `initData()` que faz fetch a `data/beta/*.json` directamente
- Botão logout

**As alterações necessárias:**

### 2.5.1 — Substituir credenciais hardcoded por chamada ao servidor

**Localizar** (perto da linha 616):
```js
if (u === VALID_U && p === VALID_P) {
  setSession(...);
  fetch('http://localhost:8080/api/login', { ... })
    .then(...);
  showApp();
}
```

**Substituir por:**
```js
fetch('/api/login', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({username: u, password: p})
})
.then(r => r.ok ? r.json() : null)
.then(d => {
  if (d?.token) {
    localStorage.setItem('fs_token', d.token);
    if (document.getElementById('loginRemember').checked) {
      localStorage.setItem('fs_remember', '1');
    }
    showApp();
  } else {
    err.textContent = 'Credenciais incorrectas.';
  }
})
.catch(() => { err.textContent = 'Erro de rede.'; });
```

**Remover as constantes `VALID_U`/`VALID_P`** (qualquer linha que as defina — auditoria com `Select-String VALID_U portfolio.html`).

### 2.5.2 — Adaptar `initData()` para usar endpoints autenticados

**Substituir** os fetches directos a `data/beta/*.json` por chamadas aos endpoints autenticados. `authFetch` é um helper reutilizável para todas as chamadas da página — inclui o token e redirige ao login em 401:

```js
function authFetch(url) {
  const tok = localStorage.getItem('fs_token') || '';
  return fetch(url + (url.includes('?') ? '&' : '?') + 't=' + Date.now(), {
    headers: { 'Authorization': 'Bearer ' + tok }
  }).then(r => {
    if (r.status === 401) {
      localStorage.removeItem('fs_token');
      location.reload();
      throw new Error('unauthorized');
    }
    return r.ok ? r.json() : null;
  }).catch(() => null);
}
```

**Mapeamento de URLs por ficheiro** (atenção aos dois prefixos):

| Ficheiro | URL para authFetch |
|---|---|
| `data/beta/beta_analysis.json` | `/api/beta/beta_analysis.json` |
| `data/beta/beta_summary.json` | `/api/beta/beta_summary.json` |
| `data/beta/beta_trades.json` | `/api/beta/beta_trades.json` |
| `data/beta/beta_equity.json` | `/api/beta/beta_equity.json` |
| `data/beta/beta_positions.json` | `/api/beta/beta_positions.json` |
| `data/beta/cro_insights.json` | `/api/beta/cro_insights.json` |
| `data/beta/regime.json` | `/api/beta/regime.json` |
| `data/beta/watchlist.json` | `/api/beta/watchlist.json` |
| `data/beta/position_meta.json` | `/api/beta/position_meta.json` |
| `data/beta/positions_ledger.json` | `/api/beta/positions_ledger.json` |
| `data/beta/earnings_ai.json` | `/api/beta/earnings_ai.json` |
| `data/beta/watchlist_fundamentals.json` | `/api/beta/watchlist_fundamentals.json` |
| `markets.json` (raiz) | `/api/data/markets.json` |
| `earnings.json` (raiz) | `/api/data/earnings.json` |
| `portfolio.json` (raiz) | `/api/portfolio` |

```js
async function initData() {
  const [ana, sum, trd, reg] = await Promise.all([
    authFetch('/api/beta/beta_analysis.json'),
    authFetch('/api/beta/beta_summary.json'),
    authFetch('/api/beta/beta_trades.json'),
    authFetch('/api/beta/regime.json'),
  ]);
  // resto do código mantém-se
}
```

### 2.5.3 — Adicionar tab "Live" como tab inicial

**No HTML do tab-bar**, adicionar como **primeiro** botão:
```html
<button class="tab-btn active" data-tab="live">Live</button>
```
Os outros (`clyde`, `bonnie`, `about`) ficam sem `active`.

**Adicionar `<section id="tabLive">`** antes do `tabClyde` actual. O conteúdo é o body de `live_portfolio.html` linhas 252–325 (Hero strip, error banner, KPI grid, tabela de posições, summary bar) com IDs prefixados ou renomeados se houver colisões. **Não copiar a `<nav>` nem o `<header>`** — esses são da página, não do tab.

**Adicionar handler do tab Live** no listener existente (≈ linha 636):
```js
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const t = btn.dataset.tab;
    document.getElementById('tabLive').style.display   = t === 'live'   ? '' : 'none';
    document.getElementById('tabClyde').style.display  = t === 'clyde'  ? '' : 'none';
    document.getElementById('tabBonnie').style.display = t === 'bonnie' ? '' : 'none';
    document.getElementById('tabAbout').style.display  = t === 'about'  ? '' : 'none';
    activeTab = t;
    if (t === 'live'  && !liveLoaded)   { loadLive();   liveLoaded = true; }
    if (t === 'bonnie' && !bonnieLoaded) { loadBonnie(); bonnieLoaded = true; }
    if (t === 'about')  { renderAbout(); }
  });
});
let activeTab = 'live';
let liveLoaded = false;
```

### 2.5.4 — Importar a lógica JS de `live_portfolio.html`

Copiar para dentro do `<script>` de `portfolio.html` as funções específicas do live (de `live_portfolio.html` linhas 499–567 aproximadamente): `loadData()`, `renderKPIs()`, `renderTable()`, `renderSummary()`, `startCountdown()`, listener de `btnRefresh`. **Renomear para evitar colisão** com `renderKPIs` que já existe no Clyde/Bonnie:
- `loadData` → `loadLive`
- `renderKPIs` → `renderLiveKPIs`
- `renderTable` → `renderLivePositions`
- `renderSummary` → `renderLiveSummary`

Substituir a fonte:
```js
const res = await fetch(`portfolio.json?t=${Date.now()}`);
```
por:
```js
const res = await fetch(`/api/portfolio?t=${Date.now()}`, {
  headers: { 'Authorization': 'Bearer ' + (localStorage.getItem('fs_token') || '') }
});
if (res.status === 401) { localStorage.removeItem('fs_token'); location.reload(); return; }
```

### 2.5.5 — Polling unificado

Substituir os dois `setInterval` desconexos (um por tab) por **um único loop** que apenas refaz fetch ao tab activo:

```js
const REFRESH_INTERVAL = 30; // segundos
setInterval(() => {
  if (activeTab === 'live')   loadLive();
  if (activeTab === 'clyde')  initData();   // recarrega beta_*
  if (activeTab === 'bonnie') loadBonnie();
}, REFRESH_INTERVAL * 1000);
```

Tabs inactivos **não** fazem fetch — preserva quota e bateria mobile.

### 2.5.6 — Logout limpa também o token

Verificar que o handler em `logoutBtn.addEventListener('click', ...)` (≈ linha 632) faz:
```js
clearSession();
localStorage.removeItem('fs_token');
location.reload();
```
(Já está parcialmente feito; confirmar.)

## 2.6 Alterações Detalhadas — `live_portfolio.html`

Substituir o ficheiro inteiro por:

```html
<!DOCTYPE html>
<html><head>
  <meta charset="UTF-8">
  <meta http-equiv="refresh" content="0;url=/portfolio.html#live">
  <script>window.location.replace('/portfolio.html#live')</script>
  <title>FundScope — Portfólio</title>
</head><body></body></html>
```

**Não apagar o ficheiro** — bookmarks antigos podem apontar para ele, e algumas service-worker caches podem servi-lo durante semanas após o deploy.

## 2.7 Alterações em Navegação (outros HTMLs)

Em **todos** os HTMLs que listam `<a href="live_portfolio.html">` no menu (auditar com `Select-String -Path *.html -Pattern 'live_portfolio\.html'`), remover essa entrada do menu. Lista mínima esperada: `live_portfolio.html` (auto-link), `markets.html`, `portfolio.html`, `news.html`, `earnings.html`, `search.html`, `watchlist.html`, `stock.html`.

Em `portfolio.html:359` há `<a href="live_portfolio.html" class="nav-link">Live Portfolio</a>` — **remover essa linha**.
Em `portfolio.html` linha ≈241 (mobile nav) — remover o item mobile equivalente.

## 2.8 Plano de Validação (E2E manual)

1. `python serve.py 8080` → confirmar que a linha "Live Portfolio: ..." já não aparece nos prompts.
2. Browser → `http://localhost:8080/portfolio.html` → vê overlay de login.
3. Login com `FUNDSCOPE_AUTH_USER`/`FUNDSCOPE_AUTH_PASSWORD` do `.env` → deve passar.
4. Login com credencial errada → mostra "Credenciais incorrectas".
5. Após login, abrir DevTools Network:
   - Tab Live: pedidos a `/api/portfolio` com `Authorization: Bearer ...` a cada 30s. KPIs e tabela populam.
   - Tab [[MOC_Clyde|Clyde]]: pedidos a `/api/beta/beta_summary.json` etc., a cada 30s **só quando [[MOC_Clyde|Clyde]] está activo**.
   - Tab [[MOC_Bonnie|Bonnie]]: idem para conteúdo Bonnie.
6. Em terminal separado: `curl http://localhost:8080/api/portfolio` (sem header) → deve devolver 401 JSON.
7. Limpar `localStorage` em DevTools → recarregar → overlay reaparece.
8. Clicar Logout → token sai do localStorage → overlay reaparece.
9. Abrir `/live_portfolio.html` → redirige para `/portfolio.html#live` automaticamente.
10. Repetir auditoria: `Select-String -Path *.html,*.js -Pattern 'https?://(chico130|localhost:8080|127\.0\.0\.1)'` → 0 hits.

## 2.9 Riscos e Mitigação

| Risco | Mitigação |
|---|---|
| Token expira a meio do dia → utilizador vê banner de erro permanente | `authFetch` faz `location.reload()` em 401 — overlay reaparece, re-login transparente. |
| `portfolio.json` desaparece (bot Trading 212 falhou) | Handler devolve 404; frontend mostra "Sem dados" no badge. **Não derrubar a página inteira.** |
| Service Worker cache antigo serve `live_portfolio.html` indefinidamente | Após deploy, bump da versão do SW (`sw.js`) força revalidação. **Mencionar ao utilizador no fim do roll-out.** |
| `.env` não carrega em produção ([[VPS_MIGRATION_SPEC|systemd]] unit sem `EnvironmentFile=`) | Documentar no commit message: "se a app não exigir login em produção, verificar `FUNDSCOPE_AUTH_PASSWORD` está no ambiente do processo." |
| CORS no fetch relativo? | Não há — fetches são same-origin. O header `Access-Control-Allow-Origin: *` em `serve.py:183` continua a ser benigno. |
| Path traversal em `/api/beta/<file>` | Whitelist `ALLOWED_BETA` — não usar `os.path.join` sem validação. |
| Path traversal em `/api/data/<file>` | Whitelist `ALLOWED_DATA` — mesma regra. |
| `markets.json`/`earnings.json` via `/api/beta/` devolve 403 | Estes ficheiros são raiz, não `data/beta/` — usar `/api/data/<file>` conforme mapeamento em §2.5.2. |

---

# Ordem de Execução Recomendada (TL;DR para o executor)

1. **Req 1** primeiro (mais barato, isolado, baixo risco):
   - 1.1 Auditoria com `Select-String`
   - 1.2 8 substituições simples
   - 1.3 `/fundscope/` → `/` (extra, mesmo passe)
   - 1.4 Validar visualmente cada página
2. **Req 2** depois (estrutural):
   - 2.4 Editar `serve.py` (env, novos handlers, whitelist)
   - 2.4 Acrescentar variáveis ao `.env`
   - 2.5.1 + 2.5.2 — Migrar auth e fetches do `portfolio.html` para os novos endpoints
   - 2.5.3–2.5.5 — Acrescentar tab Live e polling unificado
   - 2.5.6 + 2.6 — Logout cleanup + redirect file de `live_portfolio.html`
   - 2.7 — Limpar menus de navegação noutros HTMLs
   - 2.8 — Correr o plano de validação E2E

**Commit strategy sugerida:**
- Commit 1: `chore(routing): replace hardcoded URLs with root-relative paths`
- Commit 2: `feat(auth): add env-based master credential and protected JSON endpoints`
- Commit 3: `feat(portfolio): merge live portfolio into portfolio.html with unified polling`
- Commit 4: `chore(nav): retire live_portfolio.html (now redirect stub)`

**Não fazer commit num só blob** — facilita revert se a Req 2 introduzir bug e a Req 1 estiver boa.

---

# Apêndice — Checklist Rápido do Executor

- [ ] §1.1 Auditoria inicial corre e devolve as 8 linhas esperadas
- [ ] §1.3 As 8 substituições aplicadas; auditoria final vazia
- [ ] §1.4 Hot-fix paths `/fundscope/` → `/` (auditoria adicional vazia)
- [ ] §2.4 `serve.py` carrega `.env`, expõe `/api/portfolio`, `/api/beta/<file>` (ALLOWED_BETA — 12 entradas) e `/api/data/<file>` (ALLOWED_DATA — 2 entradas)
- [ ] §2.4 `.env` tem `FUNDSCOPE_AUTH_USER` e `FUNDSCOPE_AUTH_PASSWORD`
- [ ] §2.5.1 `portfolio.html` já não tem `VALID_U`/`VALID_P` hardcoded
- [ ] §2.5.2 `portfolio.html` usa `authFetch` para todos os JSONs
- [ ] §2.5.3 Tab Live existe e é o tab por defeito
- [ ] §2.5.4 Funções `loadLive/renderLive*` importadas sem colisão
- [ ] §2.5.5 Um único `setInterval` global, dispatch por `activeTab`
- [ ] §2.6 `live_portfolio.html` reduzido a stub de redirect
- [ ] §2.7 Menus dos outros HTMLs já não listam Live Portfolio
- [ ] §2.8 Plano de validação E2E completo (10 passos) — todos verdes