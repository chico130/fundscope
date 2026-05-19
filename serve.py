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
import json
import urllib.parse
import secrets
import hashlib
import time as _time
from datetime import datetime, timezone

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


def _verify_credentials(username: str, password: str) -> bool:
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
        print(f"  Live Portfolio:        http://localhost:{PORT}/live_portfolio.html")
        print(f"  Portfólio (análise):   http://localhost:{PORT}/portfolio.html")
        print(f"  Mercados:              http://localhost:{PORT}/markets.html")
        print(f"  Notícias:              http://localhost:{PORT}/news.html")
        print(f"  Earnings:              http://localhost:{PORT}/earnings.html")
        print(f"\n  Ctrl+C para parar.\n")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n  Servidor parado.")
