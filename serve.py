"""
Servidor HTTP local para o FundScope.
Resolve problemas de CORS e Tracking Prevention ao servir os ficheiros
a partir de http://localhost:8080 em vez de file://.

Uso:
    python serve.py
    python serve.py 8080      # porta personalizada
"""
import http.server
import sys
import os
import re
import json
import urllib.parse
import secrets
import hashlib
import time as _time
from datetime import datetime, timezone, timedelta

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080

# Mudar para a pasta do projecto (onde este ficheiro está)
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------
# Auth — sessões em memória, credenciais em data/user_credentials.json
# ---------------------------------------------------------------------------
_SESSIONS: dict = {}          # {token: expiry_unix_timestamp}
SESSION_TTL = 7 * 24 * 3600  # 7 dias
CREDENTIALS_PATH   = 'data/user_credentials.json'
USER_UNIVERSE_PATH = 'data/beta/user_universe.json'

# Master credential — env-first, file-fallback via _verify_credentials
AUTH_USER     = os.environ.get('FUNDSCOPE_AUTH_USER', 'admin')
AUTH_PASSWORD = os.environ.get('FUNDSCOPE_AUTH_PASSWORD', '')

# Whitelists — prevent path traversal
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
ALLOWED_DATA = {
    'markets.json',
    'earnings.json',
}
ALLOWED_LOGS = {
    'bonnie_log.json',
}

# ---------------------------------------------------------------------------
# AI Insights — on-demand Gemini endpoint (/api/ai-insight?ticker=XYZ)
# ---------------------------------------------------------------------------
AI_INSIGHTS_PATH  = 'data/beta/ai_insights.json'
AI_INSIGHTS_TTL_H = 8
AI_GEMINI_MODEL   = 'gemini-2.5-flash'
SYMBOL_CACHE_PATH = 'symbol_cache.json'


def _strip_fences(text: str) -> str:
    text = re.sub(r'^```(?:json)?\s*', '', text.strip(), flags=re.IGNORECASE)
    text = re.sub(r'\s*```$', '', text.strip())
    return text.strip()


def _parse_iso(s: str):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00'))
    except Exception:
        return None


def _is_insight_fresh(entry: dict) -> bool:
    if not entry:
        return False
    dt = _parse_iso(entry.get('generated_at', ''))
    if not dt:
        return False
    return (datetime.now(timezone.utc) - dt) < timedelta(hours=AI_INSIGHTS_TTL_H)


def _load_ai_cache() -> dict:
    try:
        with open(AI_INSIGHTS_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {'tickers': {}}


def _save_ai_cache(cache: dict) -> None:
    os.makedirs('data/beta', exist_ok=True)
    tmp = AI_INSIGHTS_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    os.replace(tmp, AI_INSIGHTS_PATH)


def _static_meta_from_symbol_cache(ticker: str) -> dict:
    try:
        with open(SYMBOL_CACHE_PATH, 'r', encoding='utf-8') as f:
            sym = json.load(f)
        for _, v in sym.items():
            if str(v.get('ticker_display', '')).upper() == ticker or \
               str(v.get('yf_ticker', '')).upper() == ticker:
                return {'name': v.get('display_name', ticker), 'currency': v.get('currency', 'USD')}
    except Exception:
        pass
    return {'name': ticker, 'currency': 'USD'}


def _build_ai_prompt(ticker: str, meta: dict) -> str:
    return (
        f"Resume em PORTUGUÊS de Portugal o contexto de mercado para o ativo abaixo.\n\n"
        f"Ticker: {ticker}\nNome: {meta['name']}\nMoeda: {meta['currency']}\n\n"
        f"Devolve um objecto JSON com exactamente estas três chaves "
        f"(cada valor é uma string curta, máximo 2 frases, sem markdown, sem listas, sem emojis):\n"
        f'{{\"sentiment\": \"...\", \"history\": \"...\", \"social\": \"...\"}}\n\n'
        f"Definições:\n"
        f"- \"sentiment\": sentimento geral do mercado nos últimos meses sobre este ativo.\n"
        f"- \"history\": breve enquadramento histórico ou de longo prazo.\n"
        f"- \"social\": perspectivas tipicamente discutidas em fóruns de investidores.\n\n"
        f"Regras obrigatórias:\n"
        f"- Sê neutro, factual e prudente. Não dês recomendação de compra/venda.\n"
        f"- Se não tens informação fiável, usa \"Informação limitada.\" nesse campo.\n"
        f"- Responde APENAS com o objecto JSON — sem texto antes, sem texto depois, sem blocos de código.\n"
    )


def _call_gemini_insight(ticker: str, meta: dict) -> dict | None:
    api_key = os.environ.get('GEMINI_API_KEY', '')
    if not api_key:
        print('[ai-insight] GEMINI_API_KEY não definido — sem chamada API', flush=True)
        return None
    raw_text = ''
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=api_key)
        raw_text = ''
        resp = client.models.generate_content(
            model=AI_GEMINI_MODEL,
            contents=_build_ai_prompt(ticker, meta),
            config=types.GenerateContentConfig(
                response_mime_type='application/json',
                temperature=0.4,
                max_output_tokens=1500,
            ),
        )
        raw_text = (resp.text or '').strip()
        if not raw_text:
            print(f'[ai-insight] resposta vazia para {ticker}', flush=True)
            return None
        data = json.loads(_strip_fences(raw_text))
        if not isinstance(data, dict):
            raise ValueError(f'resposta não é um dict: {type(data)}')
        return {
            'sentiment': str(data.get('sentiment', '')).strip()[:500],
            'history':   str(data.get('history',   '')).strip()[:500],
            'social':    str(data.get('social',    '')).strip()[:500],
        }
    except json.JSONDecodeError as e:
        preview = raw_text[:300].replace('\n', '\\n') if raw_text else '<vazio>'
        print(f'[ai-insight] JSON inválido de Gemini para {ticker}: {e}', flush=True)
        print(f'[ai-insight] raw ({len(raw_text)} chars): {preview}', flush=True)
        return None
    except Exception as e:
        print(f'[ai-insight] Gemini falhou para {ticker}: {e}', flush=True)
        return None


def _verify_credentials(username: str, password: str) -> bool:
    # 1) Master credential from .env
    if AUTH_PASSWORD and username == AUTH_USER and \
       secrets.compare_digest(password, AUTH_PASSWORD):
        return True
    # 2) Fallback — hashed credentials file
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


def _new_token() -> str:
    token = secrets.token_hex(32)
    _SESSIONS[token] = _time.time() + SESSION_TTL
    now = _time.time()
    for k in [k for k, v in _SESSIONS.items() if v < now]:
        del _SESSIONS[k]
    return token


def _valid_token(token: str) -> bool:
    if not token:
        return False
    exp = _SESSIONS.get(token)
    if exp is None:
        return False
    if _time.time() > exp:
        del _SESSIONS[token]
        return False
    return True


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == '/api/stock-review':
            self._handle_stock_review(parsed.query)
        elif parsed.path == '/api/ai-insight':
            self._handle_ai_insight(parsed.query)
        elif parsed.path == '/api/portfolio':
            self._handle_get_portfolio()
        elif parsed.path.startswith('/api/beta/'):
            self._handle_get_beta(parsed.path[len('/api/beta/'):])
        elif parsed.path.startswith('/api/data/'):
            self._handle_get_data(parsed.path[len('/api/data/'):])
        elif parsed.path.startswith('/api/logs/'):
            self._handle_get_logs(parsed.path[len('/api/logs/'):])
        else:
            super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == '/api/login':
            self._handle_login()
        elif parsed.path == '/api/save-watchlist':
            self._handle_save_watchlist()
        else:
            self.send_error(404)

    def _read_body(self) -> dict:
        length = int(self.headers.get('Content-Length', 0))
        if length > 65536:
            raise ValueError('body demasiado grande')
        raw = self.rfile.read(length) if length > 0 else b'{}'
        return json.loads(raw.decode('utf-8'))

    def _get_bearer_token(self) -> str:
        auth = self.headers.get('Authorization', '')
        if auth.startswith('Bearer '):
            return auth[7:].strip()
        return ''

    def _handle_login(self):
        try:
            body = self._read_body()
        except Exception:
            self._send_json({'error': 'JSON inválido'}, 400)
            return
        username = str(body.get('username', '')).strip()[:64]
        password = str(body.get('password', ''))[:256]
        if not username or not password:
            self._send_json({'error': 'username e password obrigatórios'}, 400)
            return
        if not _verify_credentials(username, password):
            self._send_json({'error': 'Credenciais inválidas'}, 401)
            return
        token = _new_token()
        self._send_json({'token': token, 'username': username})

    def _handle_save_watchlist(self):
        token = self._get_bearer_token()
        if not _valid_token(token):
            self._send_json({'error': 'Não autenticado — sessão inválida ou expirada'}, 401)
            return
        try:
            body = self._read_body()
        except Exception:
            self._send_json({'error': 'JSON inválido'}, 400)
            return
        tickers_raw = body.get('tickers', [])
        if not isinstance(tickers_raw, list):
            self._send_json({'error': 'tickers deve ser um array'}, 400)
            return
        tickers, seen = [], set()
        for t in tickers_raw[:200]:
            clean = ''.join(c for c in str(t).upper().strip()[:12] if c.isalnum() or c == '-')
            if clean and clean not in seen:
                seen.add(clean)
                tickers.append(clean)
        payload = {
            'last_updated': datetime.now(timezone.utc).isoformat(),
            'count': len(tickers),
            'tickers': tickers,
        }
        try:
            os.makedirs('data/beta', exist_ok=True)
            with open(USER_UNIVERSE_PATH, 'w', encoding='utf-8') as f:
                json.dump(payload, f, indent=2)
            self._send_json({'ok': True, 'count': len(tickers)})
        except Exception as e:
            self._send_json({'error': str(e)}, 500)

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

    def _handle_get_logs(self, filename: str):
        if not self._require_auth():
            return
        if filename not in ALLOWED_LOGS:
            self._send_json({'error': 'recurso não permitido'}, 403)
            return
        try:
            with open(f'logs/{filename}', 'r', encoding='utf-8') as f:
                self._send_json(json.load(f))
        except FileNotFoundError:
            self._send_json({'error': 'em falta'}, 404)
        except Exception as e:
            self._send_json({'error': str(e)}, 500)

    def _handle_stock_review(self, query_string):
        params = urllib.parse.parse_qs(query_string)
        ticker = (params.get('ticker', [''])[0]).upper().strip()
        if not ticker:
            self._send_json({'error': 'ticker is required'}, 400)
            return
        try:
            with open('data/beta/watchlist.json', 'r', encoding='utf-8') as f:
                wl = json.load(f)
            candidates = wl.get('candidates', [])
            rank = next((i + 1 for i, c in enumerate(candidates) if c.get('ticker', '').upper() == ticker), None)
            entry = next((c for c in candidates if c.get('ticker', '').upper() == ticker), None)
            if not entry:
                self._send_json({'found': False, 'ticker': ticker})
                return
            self._send_json({'found': True, 'rank': rank, 'total': len(candidates), **entry})
        except Exception as e:
            self._send_json({'error': str(e)}, 500)

    def _handle_ai_insight(self, query_string: str):
        params = urllib.parse.parse_qs(query_string)
        raw = (params.get('ticker', [''])[0]).upper().strip()
        ticker = ''.join(c for c in raw if c.isalnum() or c == '-')[:12]
        if not ticker:
            self._send_json({'error': 'ticker obrigatório'}, 400)
            return

        cache     = _load_ai_cache()
        by_ticker = cache.setdefault('tickers', {})
        entry     = by_ticker.get(ticker)

        if _is_insight_fresh(entry):
            self._send_json({**entry, 'cached': True})
            return

        print(f'[ai-insight] cache miss / stale para {ticker} — a chamar Gemini…', flush=True)
        meta   = _static_meta_from_symbol_cache(ticker)
        result = _call_gemini_insight(ticker, meta)

        if result is None:
            if entry:
                # Return stale rather than nothing
                self._send_json({**entry, 'cached': True, 'stale': True})
            else:
                self._send_json({'error': 'AI insight indisponível — tenta mais tarde'}, 503)
            return

        now_iso = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        new_entry = {
            'ticker':       ticker,
            'name':         meta['name'],
            'generated_at': now_iso,
            'model':        AI_GEMINI_MODEL,
            **result,
        }
        by_ticker[ticker] = new_entry
        cache['generated_at'] = now_iso

        try:
            _save_ai_cache(cache)
        except Exception as e:
            print(f'[ai-insight] falha a guardar cache: {e}', flush=True)

        print(f'[ai-insight] {ticker} gerado e cacheado', flush=True)
        self._send_json({**new_entry, 'cached': False})

    def _send_json(self, data, status=200):
        body = json.dumps(data).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def log_message(self, fmt, *args):
        path = str(args[0]) if args else ""
        if any(x in path for x in (".html", ".json", "/api/")):
            super().log_message(fmt, *args)


if __name__ == "__main__":
    with http.server.HTTPServer(("127.0.0.1", PORT), Handler) as httpd:
        print(f"\n  FundScope a correr em  http://localhost:{PORT}")
        print(f"  Portfólio (protegido): http://localhost:{PORT}/portfolio.html")
        print(f"  Mercados:              http://localhost:{PORT}/markets.html")
        print(f"  Notícias:              http://localhost:{PORT}/news.html")
        print(f"  Earnings:              http://localhost:{PORT}/earnings.html")
        print(f"\n  Ctrl+C para parar.\n")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n  Servidor parado.")
