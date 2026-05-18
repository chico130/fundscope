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

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080

# Mudar para a pasta do projecto (onde este ficheiro está)
os.chdir(os.path.dirname(os.path.abspath(__file__)))


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == '/api/stock-review':
            self._handle_stock_review(parsed.query)
        else:
            super().do_GET()

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
        path = args[0] if args else ""
        if any(ext in str(path) for ext in (".html", ".json", "/api/")):
            super().log_message(fmt, *args)


if __name__ == "__main__":
    with http.server.HTTPServer(("", PORT), Handler) as httpd:
        print(f"\n  FundScope a correr em  http://localhost:{PORT}")
        print(f"  Portfólio:             http://localhost:{PORT}/portfolio.html")
        print(f"  Mercados:              http://localhost:{PORT}/markets.html")
        print(f"  Notícias:              http://localhost:{PORT}/news.html")
        print(f"\n  Ctrl+C para parar.\n")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n  Servidor parado.")
