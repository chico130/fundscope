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

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080

# Mudar para a pasta do projecto (onde este ficheiro está)
os.chdir(os.path.dirname(os.path.abspath(__file__)))


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def log_message(self, fmt, *args):
        # Mostra só pedidos HTML e JSON; silencia fonts, favicons, etc.
        path = args[0] if args else ""
        if any(ext in str(path) for ext in (".html", ".json")):
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
