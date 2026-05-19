"""
Teste isolado de conectividade Telegram.

Uso:
    python test_telegram.py

Lê as credenciais de .env (se existir) ou de variáveis de ambiente já definidas.
Imprime o resultado da API e sai com código 0 (sucesso) ou 1 (falha).
"""
import json
import os
import sys
from pathlib import Path

# Carrega .env manualmente para não depender de config.py do bot
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID",   "")

if not TOKEN or not CHAT_ID:
    print("[ERRO] TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID não definidos.")
    print("       Verifica o ficheiro .env ou define as variáveis de ambiente.")
    sys.exit(1)

print(f"Token  : {TOKEN[:10]}...{TOKEN[-4:]}")
print(f"Chat ID: {CHAT_ID}")
print("A enviar mensagem de teste...")

try:
    import requests
    r = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={
            "chat_id": CHAT_ID,
            "text":    "Teste de Ping - FundScope",
        },
        timeout=10,
    )
    data = r.json()
    if data.get("ok"):
        print("[OK] Mensagem enviada com sucesso!")
        print(f"     message_id: {data['result']['message_id']}")
        sys.exit(0)
    else:
        print(f"[FALHA] API rejeitou a mensagem:")
        print(json.dumps(data, indent=2, ensure_ascii=False))
        sys.exit(1)
except Exception as exc:
    print(f"[ERRO] Excepção ao contactar a API Telegram: {exc}")
    sys.exit(1)
