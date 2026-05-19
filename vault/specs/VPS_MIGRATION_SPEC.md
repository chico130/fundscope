# FundScope — Migração para VPS Oracle Cloud (Especificação Executável)

> **Audiência**: agente de execução (Claude Sonnet / Claude Code) com SSH no VPS.
> **Alvo**: Oracle Cloud Free Tier — Ubuntu 22.04 LTS, **1 GB RAM (AMD)**, 1 vCPU, ~50 GB disco.
> **Objetivo**: zero dependência do PC local. Tudo corre 24/7 no VPS, com automação, logs, alertas e auto-restart.
> **Regra de leitura para o agente**: este documento é a única fonte de verdade. Executa **secção a secção**, não saltes. Cada bloco de comandos deve ser corrido tal como está, salvo quando indicado `<EDITAR>`.

---

## 0. Sumário Executivo & Decisões Arquiteturais

| Área | Atual (Windows) | Alvo (VPS Linux) | Porquê |
|---|---|---|---|
| Web server | `python serve.py` + `nohup` | **`serve.py` (mantido)** como `systemd` service, atrás de **Caddy** reverse proxy | Caddy dá HTTPS automático (Let's Encrypt), compressão e serve estáticos directamente. `serve.py` mantém-se intocado — só as APIs `/api/*` passam pelo proxy. |
| Agendamento | Task Scheduler + `.bat` | **`systemd` timers** (não `cron` tradicional) | Logs unificados via `journalctl`, retry policies, dependências entre units, e melhor para um free-tier que pode reiniciar. |
| Auto-restart | manual | `systemd` com `Restart=always` + `WatchdogSec` | Crash ou reboot → tudo volta sozinho. |
| Logs | `print()` + ficheiros soltos | **`journald` + `logrotate`** (apenas para JSONs aplicacionais) | Não enche o disco. Pesquisável com `journalctl -u <unit>`. |
| Alertas | nenhum | **Wrapper `run_with_alerts.sh`** + Telegram (já existe `notifier.py`) + heartbeat diário | Avisa-te se algo falha sem entrares no servidor. |
| Storage | JSONs | **JSONs (mantidos)** + cache RAM no `serve.py` + `tmpfs` para temporários | Mudar para SQLite implicaria refactor massivo do frontend (que lê JSON directo via `fetch`). O ganho não justifica em 1 GB RAM. |
| Segurança | — | UFW + fail2ban + SSH key-only + `.env` 600 | Standard para qualquer VPS público. |
| Backups | — | `tar.zst` diário de `data/` + retenção 7 dias | Pequeno custo de disco, salva-te de corrupção JSON. |

**Restrições rígidas honradas neste plano:**
- RAM total estimada do stack: **~280 MB em idle, picos ≤ 700 MB** durante `update_prices.py`/`update_portfolio.py` → cabe folgadamente em 1 GB com 2 GB de swap.
- Zero compilação pesada: nada de Postgres, nada de Docker (cada container Alpine come 80 MB), nada de Redis.
- Tudo o que adicionamos é Python puro ou binários ≤ 30 MB (Caddy, fail2ban, zstd, logrotate).

---

## 1. Preparação do VPS

### 1.1. Variáveis de referência

Define isto **uma vez** e usa em todos os comandos:

```bash
export FS_USER="ubuntu"                       # utilizador SSH
export FS_HOME="/home/${FS_USER}"
export FS_DIR="${FS_HOME}/fundscope"          # raiz do projecto no VPS
export FS_REPO="git@github.com:<TEU_USER>/<TEU_REPO>.git"   # <EDITAR>
export FS_DOMAIN="fundscope.example.com"      # <EDITAR> — ou deixar vazio para IP-only TLS
```

### 1.2. Sistema base, timezone, swap

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-venv python3-pip git curl ufw fail2ban \
                    logrotate zstd jq unzip ca-certificates

# Timezone Europe/Lisbon (críticos para cron de market hours)
sudo timedatectl set-timezone Europe/Lisbon
timedatectl

# Swap 2 GB — colchão de segurança para picos do yfinance/pandas
if [ ! -f /swapfile ]; then
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
fi

# Reduzir agressividade do swap (preferir RAM)
echo 'vm.swappiness=10'           | sudo tee /etc/sysctl.d/99-fundscope.conf
echo 'vm.vfs_cache_pressure=50'   | sudo tee -a /etc/sysctl.d/99-fundscope.conf
sudo sysctl --system
```

### 1.3. Firewall (UFW)

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp                 # SSH
sudo ufw allow 80/tcp                 # HTTP (Caddy → redirect 443)
sudo ufw allow 443/tcp                # HTTPS
# NOTA: porta 8080 NÃO é exposta. serve.py só escuta em 127.0.0.1.
sudo ufw --force enable
sudo ufw status verbose
```

> **Importante na Oracle Cloud**: além do UFW, abre 80 e 443 na **Security List** da VCN (consola Oracle → Networking → Virtual Cloud Networks → Subnet → Security List → Ingress Rules). O firewall da Oracle vive *acima* do VPS.

### 1.4. fail2ban (proteção SSH)

```bash
sudo tee /etc/fail2ban/jail.d/sshd.local > /dev/null <<'EOF'
[sshd]
enabled  = true
port     = ssh
maxretry = 5
findtime = 10m
bantime  = 1h
EOF

sudo systemctl enable --now fail2ban
sudo fail2ban-client status sshd
```

---

## 2. Deploy do Código

### 2.1. Clone do repositório

```bash
sudo mkdir -p "${FS_DIR}"
sudo chown "${FS_USER}:${FS_USER}" "${FS_DIR}"

# Se já existe SSH key configurada no GitHub:
git clone "${FS_REPO}" "${FS_DIR}"

# Se ainda não existe, gera e adiciona ao GitHub:
# ssh-keygen -t ed25519 -C "fundscope-vps" -f ~/.ssh/id_ed25519 -N ""
# cat ~/.ssh/id_ed25519.pub   # copiar para GitHub → Settings → SSH keys
```

### 2.2. Virtualenv & dependências

```bash
cd "${FS_DIR}"
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip wheel
pip install -r requirements.txt

# Dependências adicionais para o stack VPS:
pip install python-telegram-bot==13.15  # se notifier.py ainda não tem
# Nota: não instalamos Gunicorn/Waitress — serve.py continua a usar http.server stdlib.
```

### 2.3. Ficheiro `.env` (secrets)

```bash
sudo install -m 600 -o "${FS_USER}" -g "${FS_USER}" /dev/null "${FS_DIR}/.env"
nano "${FS_DIR}/.env"
```

Conteúdo (preencher):

```ini
# APIs externas
FINNHUB_TOKEN=...
MARKETAUX_TOKEN=...
ALPHAVANTAGE_TOKEN=...
NEWSAPI_TOKEN=...
T212_API_ID=...
T212_API_KEY=...
GEMINI_API_KEY=...

# Telegram (alertas)
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...

# Operação
FS_ENV=production
FS_TZ=Europe/Lisbon
```

### 2.4. Git push automático do `update_portfolio.py` no VPS

O script atual faz `git add/commit/push` no fim. Para funcionar no VPS sem prompts:

```bash
# Identidade git local do bot
git -C "${FS_DIR}" config user.name  "FundScope VPS Bot"
git -C "${FS_DIR}" config user.email "bot@fundscope.local"

# Garantir que o remote usa SSH (não HTTPS)
git -C "${FS_DIR}" remote set-url origin "${FS_REPO}"

# Testar sem prompts
sudo -u "${FS_USER}" GIT_SSH_COMMAND='ssh -o BatchMode=yes' \
     git -C "${FS_DIR}" ls-remote origin HEAD
```

### 2.5. tmpfs para `__pycache__` (opcional, poupa I/O)

```bash
# Coloca __pycache__ em RAM (limpa a cada reboot, sem impacto funcional)
sudo tee -a /etc/fstab > /dev/null <<EOF
tmpfs ${FS_DIR}/__pycache__       tmpfs defaults,size=20M,uid=1000,gid=1000 0 0
tmpfs ${FS_DIR}/bot/__pycache__   tmpfs defaults,size=20M,uid=1000,gid=1000 0 0
EOF
sudo mkdir -p "${FS_DIR}/__pycache__" "${FS_DIR}/bot/__pycache__"
sudo mount -a
```

---

## 3. Servidor Web — Caddy + `serve.py` via systemd

### 3.1. Alterar `serve.py` para escutar **apenas em 127.0.0.1**

Edita `serve.py` linha 193:

```python
# ANTES
with http.server.HTTPServer(("", PORT), Handler) as httpd:

# DEPOIS
with http.server.HTTPServer(("127.0.0.1", PORT), Handler) as httpd:
```

> **Porquê**: o Caddy fala com o `serve.py` via loopback. Nada de externo toca em `serve.py` directamente — Caddy é a única porta de entrada pública.

### 3.2. Instalar Caddy

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy
```

### 3.3. Configurar Caddy

```bash
sudo tee /etc/caddy/Caddyfile > /dev/null <<EOF
# Se tens domínio, substitui ${FS_DOMAIN}. Sem domínio: usa :80 e deixa HTTP only,
# ou compra/cria um DuckDNS gratuito.
${FS_DOMAIN} {
    encode zstd gzip

    # Estáticos (HTML, JSON, CSS, imagens) — servidos directamente pelo Caddy
    root * ${FS_DIR}
    file_server

    # APIs Python passam pelo serve.py
    @api path /api/*
    reverse_proxy @api 127.0.0.1:8080

    # Segurança básica
    header {
        X-Content-Type-Options "nosniff"
        Referrer-Policy "strict-origin-when-cross-origin"
        # NÃO definir Cache-Control aqui — o serve.py já manda no-cache para JSONs
    }

    # JSONs nunca devem ser cacheados (são gerados pelos updates)
    @json path *.json
    header @json Cache-Control "no-cache, no-store, must-revalidate"

    log {
        output file /var/log/caddy/fundscope.log {
            roll_size 20mb
            roll_keep 5
        }
        format json
    }
}
EOF

sudo systemctl reload caddy
sudo journalctl -u caddy -n 50 --no-pager
```

> Se **não tiveres domínio**, substitui o bloco `${FS_DOMAIN} {` por `:80 {` (sem TLS) ou `:443 { tls internal` (self-signed). Recomendação: regista grátis em [duckdns.org](https://www.duckdns.org/) para teres HTTPS válido.

### 3.4. Systemd service para `serve.py`

```bash
sudo tee /etc/systemd/system/fundscope-web.service > /dev/null <<EOF
[Unit]
Description=FundScope Web (serve.py)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${FS_USER}
WorkingDirectory=${FS_DIR}
EnvironmentFile=${FS_DIR}/.env
ExecStart=${FS_DIR}/.venv/bin/python ${FS_DIR}/serve.py 8080
Restart=always
RestartSec=5s
StandardOutput=journal
StandardError=journal
SyslogIdentifier=fundscope-web

# Hardening + limites (1 GB RAM box)
MemoryMax=200M
CPUQuota=50%
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=false
ReadWritePaths=${FS_DIR}

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now fundscope-web
sudo systemctl status fundscope-web --no-pager
```

### 3.5. Smoke test

```bash
curl -fsS http://127.0.0.1:8080/index.html | head -n 5
curl -fsS https://${FS_DOMAIN}/index.html | head -n 5
curl -fsS https://${FS_DOMAIN}/data.json | jq '.updated // "no field"'
```

---

## 4. Automação dos Updates — Systemd Timers

> Princípio: **um service por script + um timer por agenda**. Logs ficam em `journalctl -u <service>`.

### 4.1. Template genérico

Cria um helper para arrancarem todos com o `.venv` activo e o `.env` carregado:

```bash
sudo tee /usr/local/bin/fs-run > /dev/null <<'EOF'
#!/usr/bin/env bash
# Wrapper: corre um script Python do FundScope com venv + .env + alertas Telegram.
set -euo pipefail

SCRIPT="${1:?usage: fs-run <script.py>}"
FS_DIR="${FS_DIR:-/home/ubuntu/fundscope}"

cd "${FS_DIR}"
set -a
source "${FS_DIR}/.env"
set +a

LOGTAG="fs-$(basename "${SCRIPT}" .py)"
START=$(date +%s)

if ! "${FS_DIR}/.venv/bin/python" "${FS_DIR}/${SCRIPT}"; then
    DUR=$(( $(date +%s) - START ))
    MSG="🔴 FundScope: ${SCRIPT} FALHOU após ${DUR}s. Ver: journalctl -u ${LOGTAG}.service -n 100"
    if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
        curl -fsS -X POST \
          "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
          -d "chat_id=${TELEGRAM_CHAT_ID}" \
          -d "text=${MSG}" > /dev/null || true
    fi
    exit 1
fi
EOF

sudo chmod +x /usr/local/bin/fs-run
```

### 4.2. Função-bash auxiliar para criar units rapidamente

Cria isto **uma vez** e usa para gerar todas as units:

```bash
create_fs_timer() {
  local NAME="$1"          # ex: prices
  local SCRIPT="$2"        # ex: update_prices.py
  local SCHEDULE="$3"      # systemd OnCalendar string
  local DESC="$4"

  sudo tee /etc/systemd/system/fs-${NAME}.service > /dev/null <<EOF
[Unit]
Description=${DESC}
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=${FS_USER}
Environment=FS_DIR=${FS_DIR}
ExecStart=/usr/local/bin/fs-run ${SCRIPT}
StandardOutput=journal
StandardError=journal
SyslogIdentifier=fs-${NAME}
MemoryMax=600M
CPUQuota=80%
TimeoutStartSec=20min
EOF

  sudo tee /etc/systemd/system/fs-${NAME}.timer > /dev/null <<EOF
[Unit]
Description=Timer: ${DESC}

[Timer]
OnCalendar=${SCHEDULE}
Persistent=true
RandomizedDelaySec=30s
Unit=fs-${NAME}.service

[Install]
WantedBy=timers.target
EOF
}
```

### 4.3. Definir todos os timers

Tabela de agendamento proposta (timezone do sistema = Europe/Lisbon):

| Script | Frequência | `OnCalendar` | Notas |
|---|---|---|---|
| `update_prices.py` | 15min, só durante horário US | `Mon..Fri 14:30..21:00/15:00` | Mercado US: 14:30–21:00 LX |
| `update_portfolio.py` | de hora a hora, horário US + 1h depois | `Mon..Fri *:05:00` | T212 + git push |
| `update_news.py` | 30min | `*:00,30:00` | RSS + APIs externas |
| `update_markets.py` | de hora a hora | `*:10:00` | Sectores |
| `update_earnings.py` | diário 07:00 | `*-*-* 07:00:00` | Calendário 14 dias |
| `bot.bonnie` | de hora a hora | `*:20:00` | Risco / oversight |
| `bot.main` (Clyde) | minuto-a-minuto, horário US | `Mon..Fri 14:30..21:00:00/1min` | Decisões em ciclo curto |

> ⚠️ **Antes de criar `bot.main` como timer**: o Clyde original era um **loop contínuo** com `Ligar_Bot.bat` + `schtasks /k`. Decide com o utilizador se queres (a) executar 1× por minuto em modo "tick" (recomendado para systemd), ou (b) torná-lo um *daemon* permanente como o `fundscope-web`. Se (b), cria um service `fundscope-bot.service` em vez do timer abaixo. Ver §4.5.

Comandos (executar tudo de seguida):

```bash
create_fs_timer prices    update_prices.py    "Mon..Fri 14:30..21:00:00/15min"  "FundScope: update prices (US market hours)"
create_fs_timer portfolio update_portfolio.py "Mon..Fri *:05:00"                "FundScope: T212 portfolio sync + git push"
create_fs_timer news      update_news.py      "*:00,30:00"                       "FundScope: refresh news feeds"
create_fs_timer markets   update_markets.py   "*:10:00"                          "FundScope: refresh sector markets"
create_fs_timer earnings  update_earnings.py  "*-*-* 07:00:00"                   "FundScope: earnings calendar (14d)"
create_fs_timer bonnie    -m bot.bonnie       "*:20:00"                          "FundScope: Bonnie risk oversight"
# 'create_fs_timer' acima passa o script tal-qual; para módulos, usa o helper alternativo abaixo.

sudo systemctl daemon-reload

for t in prices portfolio news markets earnings bonnie; do
  sudo systemctl enable --now fs-${t}.timer
done

systemctl list-timers --all | grep fs-
```

### 4.4. Service alternativo para módulos Python (`python -m bot.bonnie`)

O wrapper `fs-run` espera um ficheiro `.py`. Para módulos, cria um service dedicado:

```bash
sudo tee /etc/systemd/system/fs-bonnie.service > /dev/null <<EOF
[Unit]
Description=FundScope: Bonnie risk oversight
After=network-online.target

[Service]
Type=oneshot
User=${FS_USER}
WorkingDirectory=${FS_DIR}
EnvironmentFile=${FS_DIR}/.env
ExecStart=${FS_DIR}/.venv/bin/python -m bot.bonnie
StandardOutput=journal
StandardError=journal
SyslogIdentifier=fs-bonnie
MemoryMax=400M
TimeoutStartSec=10min
EOF
sudo systemctl daemon-reload
```

### 4.5. Decisão: Clyde como timer ou daemon?

**Opção A — Timer (recomendado)**: 1 invocação por minuto, exit code controla falhas, sem risco de leak de memória entre ciclos.

```bash
create_fs_timer clyde -m bot.main "Mon..Fri 14:30..21:00:00/1min" "FundScope: Clyde tick"
```

**Opção B — Daemon permanente**: se `bot/main.py` for desenhado para correr indefinidamente com loop interno.

```bash
sudo tee /etc/systemd/system/fundscope-clyde.service > /dev/null <<EOF
[Unit]
Description=FundScope: Clyde main loop
After=network-online.target

[Service]
Type=simple
User=${FS_USER}
WorkingDirectory=${FS_DIR}
EnvironmentFile=${FS_DIR}/.env
ExecStart=${FS_DIR}/.venv/bin/python -m bot.main
Restart=always
RestartSec=15s
MemoryMax=500M
CPUQuota=70%
SyslogIdentifier=fundscope-clyde

# Watchdog: se o processo não der sinal de vida em 10min, mata e reinicia
WatchdogSec=600s

[Install]
WantedBy=multi-user.target
EOF
# Activar SÓ se Clyde for daemon:
# sudo systemctl enable --now fundscope-clyde
```

> **Confirma com o utilizador qual é o modelo correto** antes de activar. Pista: se existe um `while True:` no `bot/main.py`, é Opção B.

---

## 5. Logs & Rotação

### 5.1. `journald` — limitar uso de disco

```bash
sudo mkdir -p /etc/systemd/journald.conf.d
sudo tee /etc/systemd/journald.conf.d/fundscope.conf > /dev/null <<'EOF'
[Journal]
Storage=persistent
SystemMaxUse=500M
SystemKeepFree=200M
SystemMaxFileSize=50M
MaxRetentionSec=14day
EOF
sudo systemctl restart systemd-journald
```

### 5.2. `logrotate` para os JSONs aplicacionais (`logs/`)

```bash
sudo tee /etc/logrotate.d/fundscope > /dev/null <<EOF
${FS_DIR}/logs/*.json ${FS_DIR}/logs/*.log {
    daily
    rotate 14
    compress
    compresscmd /usr/bin/zstd
    compressext .zst
    compressoptions -19
    missingok
    notifempty
    copytruncate
    su ${FS_USER} ${FS_USER}
}
EOF

sudo logrotate -d /etc/logrotate.d/fundscope    # dry-run
sudo logrotate    /etc/logrotate.d/fundscope    # forçar 1x
```

### 5.3. Comandos diários de inspeção

```bash
# Ver últimos eventos de qualquer unit FundScope
journalctl -u 'fs-*' -u fundscope-web --since "1h ago" --no-pager

# Erros nas últimas 24h
journalctl -p err --since "24h ago" | grep -i fundscope

# Stream em tempo real
journalctl -u fundscope-web -f
```

---

## 6. Monitorização Leve

### 6.1. Alertas em falha (já cobertos por `fs-run`)

Qualquer script que falhar dispara mensagem Telegram com o output que o utilizador precisa para investigar.

### 6.2. Heartbeat diário "tudo OK"

```bash
sudo tee /usr/local/bin/fs-heartbeat > /dev/null <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
FS_DIR="${FS_DIR:-/home/ubuntu/fundscope}"
source "${FS_DIR}/.env"

# Idade dos JSONs principais (em minutos)
age_min() { echo $(( ($(date +%s) - $(stat -c %Y "$1")) / 60 )); }

DATA_AGE=$(age_min "${FS_DIR}/data.json"      2>/dev/null || echo "?")
NEWS_AGE=$(age_min "${FS_DIR}/news.json"      2>/dev/null || echo "?")
MKT_AGE=$(age_min  "${FS_DIR}/markets.json"   2>/dev/null || echo "?")
PORT_AGE=$(age_min "${FS_DIR}/portfolio.json" 2>/dev/null || echo "?")
EARN_AGE=$(age_min "${FS_DIR}/earnings.json"  2>/dev/null || echo "?")

DISK=$(df -h / | awk 'NR==2 {print $5" used"}')
RAM=$(free -m | awk '/Mem:/ {printf "%.0f%% (%d/%d MB)", $3*100/$2, $3, $2}')
LOAD=$(cut -d' ' -f1-3 /proc/loadavg)

MSG="✅ FundScope OK — $(date -Iminutes)
data: ${DATA_AGE}m | news: ${NEWS_AGE}m | mkt: ${MKT_AGE}m | port: ${PORT_AGE}m | earn: ${EARN_AGE}m
RAM ${RAM} | disk ${DISK} | load ${LOAD}"

curl -fsS -X POST \
  "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
  --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
  --data-urlencode "text=${MSG}" > /dev/null
EOF
sudo chmod +x /usr/local/bin/fs-heartbeat
```

Service + timer:

```bash
sudo tee /etc/systemd/system/fs-heartbeat.service > /dev/null <<EOF
[Unit]
Description=FundScope daily heartbeat
[Service]
Type=oneshot
User=${FS_USER}
Environment=FS_DIR=${FS_DIR}
ExecStart=/usr/local/bin/fs-heartbeat
EOF

sudo tee /etc/systemd/system/fs-heartbeat.timer > /dev/null <<'EOF'
[Unit]
Description=Daily FundScope heartbeat (09:00 LX)
[Timer]
OnCalendar=*-*-* 09:00:00
Persistent=true
[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now fs-heartbeat.timer
```

### 6.3. Alerta de "stale data"

Adicional: avisa se algum JSON crítico não foi actualizado há mais que X minutos durante horário de mercado.

```bash
sudo tee /usr/local/bin/fs-staleness-check > /dev/null <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
FS_DIR="${FS_DIR:-/home/ubuntu/fundscope}"
source "${FS_DIR}/.env"

check() {
  local FILE="$1"; local MAX_MIN="$2"
  [ -f "$FILE" ] || return 0
  local AGE=$(( ($(date +%s) - $(stat -c %Y "$FILE")) / 60 ))
  if [ "$AGE" -gt "$MAX_MIN" ]; then
    curl -fsS -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
      --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
      --data-urlencode "text=⚠️ FundScope: $(basename "$FILE") tem ${AGE}min (limite ${MAX_MIN}min)" > /dev/null
  fi
}

# Só alerta durante horário de mercado US (14:30–21:00 LX, dias úteis)
HOUR=$(date +%H); DOW=$(date +%u)
if [ "$DOW" -le 5 ] && [ "$HOUR" -ge 15 ] && [ "$HOUR" -lt 21 ]; then
  check "${FS_DIR}/data.json"      30
  check "${FS_DIR}/portfolio.json" 90
fi
EOF
sudo chmod +x /usr/local/bin/fs-staleness-check
```

Timer de 20min:

```bash
sudo tee /etc/systemd/system/fs-staleness.service > /dev/null <<EOF
[Unit]
Description=FundScope staleness check
[Service]
Type=oneshot
User=${FS_USER}
Environment=FS_DIR=${FS_DIR}
ExecStart=/usr/local/bin/fs-staleness-check
EOF

sudo tee /etc/systemd/system/fs-staleness.timer > /dev/null <<'EOF'
[Unit]
Description=Run staleness check every 20 min
[Timer]
OnCalendar=*:00,20,40
Persistent=false
[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now fs-staleness.timer
```

---

## 7. Resiliência (reboot survival)

Já configurada em §3.4 e §4. Validação:

```bash
# Listar tudo que arranca no boot
systemctl list-unit-files --state=enabled | grep -E '(fundscope|fs-|caddy|fail2ban)'

# Teste real de reboot
sudo reboot
# Depois de reconectar SSH:
systemctl status fundscope-web caddy
systemctl list-timers --all | grep fs-
curl -fsS https://${FS_DOMAIN}/index.html | head -n 3
```

Se algum service falhar 3× seguidas, systemd entra em `failed`. Para evitar isso e forçar retry contínuo, podes adicionar a `[Service]`:

```ini
Restart=always
StartLimitIntervalSec=0
```

---

## 8. Backups (`data/` + JSONs raiz)

```bash
sudo tee /usr/local/bin/fs-backup > /dev/null <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
FS_DIR="${FS_DIR:-/home/ubuntu/fundscope}"
DEST="/var/backups/fundscope"
mkdir -p "${DEST}"

STAMP=$(date +%Y%m%d_%H%M)
tar --zstd -cf "${DEST}/fs-${STAMP}.tar.zst" \
    -C "${FS_DIR}" data data.json portfolio.json news.json markets.json earnings.json logs 2>/dev/null || true

# Retenção 7 dias
find "${DEST}" -name 'fs-*.tar.zst' -mtime +7 -delete
EOF
sudo chmod +x /usr/local/bin/fs-backup

sudo tee /etc/systemd/system/fs-backup.service > /dev/null <<EOF
[Unit]
Description=FundScope nightly backup
[Service]
Type=oneshot
User=root
Environment=FS_DIR=${FS_DIR}
ExecStart=/usr/local/bin/fs-backup
EOF

sudo tee /etc/systemd/system/fs-backup.timer > /dev/null <<'EOF'
[Unit]
Description=Daily FundScope backup (03:30 LX)
[Timer]
OnCalendar=*-*-* 03:30:00
Persistent=true
[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now fs-backup.timer
```

---

## 9. Segurança (Checklist)

- [ ] SSH key-only (`PasswordAuthentication no` em `/etc/ssh/sshd_config`)
- [ ] UFW activo (§1.3)
- [ ] fail2ban a correr (§1.4)
- [ ] `.env` com `chmod 600`
- [ ] `serve.py` apenas em `127.0.0.1` (§3.1)
- [ ] Caddy com HTTPS automático
- [ ] Apenas portas 22/80/443 expostas (Security List da Oracle)
- [ ] `git` remote usa SSH (não HTTPS com token em plain text)
- [ ] `unattended-upgrades` activo:

```bash
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades
```

---

## 10. Upgrades Arquiteturais Opcionais

> **Atenção**: estes upgrades são **opt-in**. Implementar apenas se o utilizador validar — adicionam complexidade.

### 10.1. Cache RAM no `serve.py` (recomendado)

Os HTMLs fazem `fetch('data.json')` várias vezes. Cada fetch lê o disco. Em alta concorrência (improvável aqui, mas para principio):

Adicionar em `serve.py` antes da `class Handler`:

```python
import threading
_JSON_CACHE: dict = {}      # path -> (mtime, bytes)
_JSON_LOCK = threading.Lock()

def _serve_json_cached(path: str) -> bytes | None:
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    with _JSON_LOCK:
        cached = _JSON_CACHE.get(path)
        if cached and cached[0] == mtime:
            return cached[1]
        with open(path, 'rb') as f:
            data = f.read()
        _JSON_CACHE[path] = (mtime, data)
        # Cap em 50 MB total
        if sum(len(v[1]) for v in _JSON_CACHE.values()) > 50 * 1024 * 1024:
            _JSON_CACHE.pop(next(iter(_JSON_CACHE)))
        return data
```

E intercepta no `do_GET` para paths terminados em `.json`.

> Custo: ~10 MB RAM, zero I/O em hot path.

### 10.2. SQLite para estado interno do bot (NÃO para o frontend)

`bot/position_ledger.py`, `bot/learner.py`, `bot/diario_trades.json` poderiam migrar para um `state.db` SQLite com WAL — escrita atómica, sem corromper em crashes.

**Não migrar** os JSONs lidos pelo frontend (`data.json`, `portfolio.json`, etc.). O frontend é puro `fetch` + JS; mudar implicava reescrever camada de leitura.

### 10.3. Healthchecks.io (alternativa ao heartbeat caseiro)

Cada timer pings um URL ao terminar com sucesso. Se falhar, o serviço notifica por email/Telegram. Gratuito até 20 checks. Vantagem: detecta também o caso onde o **VPS inteiro está down** (o heartbeat caseiro não conseguiria avisar).

Adicionar ao final de `fs-run`:

```bash
[ -n "${HEALTHCHECKS_${LOGTAG^^}_URL:-}" ] && curl -fsS --retry 3 "${HEALTHCHECKS_URL}" > /dev/null || true
```

### 10.4. Uptime Kuma (NÃO recomendado em 1 GB RAM)

Boa ferramenta mas come 150 MB. Em 1 GB RAM com toda a stack, ficas no fio. Skip.

---

## 11. Checklist Final de Validação

Após implementação, o agente deve confirmar todos os pontos:

```bash
# 1. Web está vivo
curl -fsS -o /dev/null -w "%{http_code}\n" "https://${FS_DOMAIN}/"           # 200
curl -fsS -o /dev/null -w "%{http_code}\n" "https://${FS_DOMAIN}/data.json"  # 200

# 2. APIs funcionam
curl -fsS -X POST "https://${FS_DOMAIN}/api/login" \
  -H 'Content-Type: application/json' \
  -d '{"username":"x","password":"y"}'    # devolve 401 (credenciais inválidas) — OK

# 3. Todos os timers carregaram
systemctl list-timers --all | grep -c fs-       # ≥ 6

# 4. Próximos disparos
systemctl list-timers fs-*

# 5. Nenhuma unit em estado 'failed'
systemctl --failed

# 6. RAM/disco saudáveis
free -h
df -h /

# 7. Logs recentes sem erros
journalctl -u 'fs-*' -u fundscope-web -u caddy --since "1h ago" -p warning

# 8. Heartbeat Telegram já chegou
journalctl -u fs-heartbeat.service --since "today"

# 9. Push automático funciona
sudo -u ${FS_USER} git -C ${FS_DIR} status
sudo -u ${FS_USER} git -C ${FS_DIR} log --oneline -5

# 10. Reboot survival
sudo reboot   # esperar 60s, reconectar, repetir 1+3+5
```

---

## 12. Cheat Sheet de Operação Diária

```bash
# Logs em tempo real do web
journalctl -u fundscope-web -f

# Última execução de qualquer update
journalctl -u fs-prices.service -n 200 --no-pager

# Forçar execução manual de um update
sudo systemctl start fs-prices.service

# Pausar um timer (ex: durante manutenção)
sudo systemctl stop fs-prices.timer

# Reativar
sudo systemctl start fs-prices.timer

# Update de código (git pull + restart)
cd ${FS_DIR}
git pull
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart fundscope-web
# (timers usam o código fresh na próxima execução, sem restart)

# Ver consumo do FundScope agora
systemctl status fundscope-web --no-pager
systemd-cgtop -1 -n 1

# Restaurar de backup
sudo tar --zstd -xf /var/backups/fundscope/fs-YYYYMMDD_HHMM.tar.zst -C ${FS_DIR}
sudo systemctl restart fundscope-web
```

---

## Apêndice A — Decisões Tomadas vs Rejeitadas

| Considerado | Decisão | Razão |
|---|---|---|
| Nginx vs Caddy | **Caddy** | Auto-HTTPS, ficheiro de config 5× mais curto, idêntico em performance para esta carga. |
| Gunicorn/Waitress/uWSGI | **Manter `http.server`** | `serve.py` não é Flask/WSGI. Reescrever é 1 dia de trabalho sem benefício real para carga ≤ 5 req/s. |
| cron vs systemd timer | **systemd timer** | Logs unificados, `Persistent=true` recupera execuções perdidas após reboot, dependências entre units. |
| Docker | **Não** | 80 MB por container num VPS de 1 GB é desproporcionado para 1 app. |
| PostgreSQL/Redis | **Não** | JSON é suficiente, frontend depende disso, RAM é precioso. |
| SQLite (frontend) | **Não** | Quebra o frontend actual. |
| SQLite (bot interno) | **Opcional** | Útil para `position_ledger` mas não bloqueante. |
| Uptime Kuma | **Não** | 150 MB RAM. Substituído por heartbeat Telegram + Healthchecks.io. |
| Tailscale / WireGuard | **Não decidido** | Útil se quiseres SSH sem expor porta 22. Decidir caso a caso. |

---

## Apêndice B — Tempo estimado de execução

| Secção | Duração | Bloqueante? |
|---|---|---|
| 1. Preparação VPS | 10 min | sim |
| 2. Deploy código | 15 min | sim |
| 3. Web server (Caddy + serve.py) | 15 min | sim |
| 4. Timers | 20 min | sim |
| 5. Logs | 5 min | não |
| 6. Monitorização | 10 min | não |
| 7. Validação reboot | 5 min | sim |
| 8. Backups | 5 min | não |
| **Total** | **~85 min** | |

---

**Fim da especificação.** O agente executor deve, após cada secção, reportar ao utilizador: `✅ Secção N concluída` ou `❌ Bloqueado em N porque...`.