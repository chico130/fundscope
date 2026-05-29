#!/usr/bin/env bash
# scripts/vps_repair.sh
# Diagnóstica e repara as fs-update_*.service quando falham a 0-1s.
# Correr no VPS: bash ~/fundscope/scripts/vps_repair.sh
set -euo pipefail

FS_DIR="${FS_DIR:-/home/ubuntu/fundscope}"
VENV_PYTHON="${FS_DIR}/.venv/bin/python"
ENV_FILE="${FS_DIR}/.env"
REPO_SYSTEMD="${FS_DIR}/systemd"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[OK]${NC} $*"; }
warn() { echo -e "${YELLOW}[AVISO]${NC} $*"; }
err()  { echo -e "${RED}[ERRO]${NC} $*"; }

# ─── Passo 0: ver o erro real ────────────────────────────────────────────────
echo "=== PASSO 0: últimas 30 linhas do journal (fs-update_news) ==="
journalctl -u fs-update_news.service -n 30 --no-pager 2>/dev/null || warn "service não encontrada ainda"
echo ""

# ─── Passo 1: validar venv ───────────────────────────────────────────────────
echo "=== PASSO 1: venv ==="
VENV_BROKEN=false

if [ ! -f "${VENV_PYTHON}" ]; then
    err "Binário ausente: ${VENV_PYTHON}"
    VENV_BROKEN=true
elif [ ! -x "${VENV_PYTHON}" ]; then
    err "Sem permissão exec: ${VENV_PYTHON}"
    VENV_BROKEN=true
else
    # Testa se o python resolve — symlink pendente dá status 127
    if ! "${VENV_PYTHON}" --version > /dev/null 2>&1; then
        err "Symlink pendente (python3.X removido pelo unattended-upgrades?): ${VENV_PYTHON}"
        ls -la "$(dirname "${VENV_PYTHON}")/python"* 2>/dev/null || true
        VENV_BROKEN=true
    else
        ok "$("${VENV_PYTHON}" --version)"
        # Verificar imports críticos
        if "${VENV_PYTHON}" -c "import requests, yfinance, pandas, numpy" 2>/dev/null; then
            ok "Imports críticos OK (requests, yfinance, pandas, numpy)"
        else
            err "Imports críticos falham — dependências partidas"
            VENV_BROKEN=true
        fi
    fi
fi

# ─── Passo 2: reparar venv ───────────────────────────────────────────────────
echo ""
echo "=== PASSO 2: reparar ==="

if $VENV_BROKEN; then
    warn "A recriar venv (pode demorar 2-4 minutos)..."
    rm -rf "${FS_DIR}/.venv"
    python3 -m venv "${FS_DIR}/.venv"
    "${FS_DIR}/.venv/bin/pip" install --upgrade pip wheel --quiet
    "${FS_DIR}/.venv/bin/pip" install -r "${FS_DIR}/requirements.txt" --quiet
    if "${VENV_PYTHON}" -c "import requests, yfinance, pandas, numpy" 2>/dev/null; then
        ok "Venv recriado com sucesso"
    else
        err "Falha mesmo depois de recriar — ver output acima"
        exit 1
    fi
else
    ok "Venv OK — sem necessidade de recriação"
fi

# Verificar .env
echo ""
if [ ! -f "${ENV_FILE}" ]; then
    err ".env não encontrado em ${ENV_FILE} — criar e preencher com os secrets antes de continuar"
    exit 1
else
    PERMS=$(stat -c "%a" "${ENV_FILE}")
    if [ "${PERMS}" != "600" ]; then
        warn ".env com permissões ${PERMS} — a corrigir para 600"
        chmod 600 "${ENV_FILE}"
    fi
    ok ".env presente (${PERMS} → 600)"
fi

# Verificar espaço em disco
DISK_PCT=$(df / | awk 'NR==2 {gsub(/%/,"",$5); print $5}')
if [ "${DISK_PCT}" -ge 90 ]; then
    warn "Disco a ${DISK_PCT}% — a limpar journal antigo"
    sudo journalctl --vacuum-size=100M
else
    ok "Disco OK (${DISK_PCT}% usado)"
fi

# ─── Passo 3: deploy service files do repo ──────────────────────────────────
echo ""
echo "=== PASSO 3: deploy units ==="

SERVICES=(fs-update_prices fs-update_news fs-update_portfolio fs-update_markets)

for SVC in "${SERVICES[@]}"; do
    SVC_FILE="${REPO_SYSTEMD}/${SVC}.service"
    TIMER_FILE="${REPO_SYSTEMD}/${SVC}.timer"
    if [ -f "${SVC_FILE}" ]; then
        sudo cp "${SVC_FILE}" /etc/systemd/system/
        ok "Copiado ${SVC}.service"
    else
        warn "${SVC_FILE} não encontrado no repo — a manter a versão existente"
    fi
    if [ -f "${TIMER_FILE}" ]; then
        sudo cp "${TIMER_FILE}" /etc/systemd/system/
        ok "Copiado ${SVC}.timer"
    fi
done

# Deploy fs-run actualizado
FS_RUN_REPO="${REPO_SYSTEMD}/fs-run"
if [ -f "${FS_RUN_REPO}" ]; then
    sudo cp "${FS_RUN_REPO}" /usr/local/bin/fs-run
    sudo chmod +x /usr/local/bin/fs-run
    ok "fs-run actualizado em /usr/local/bin/fs-run"
fi

sudo systemctl daemon-reload

for SVC in "${SERVICES[@]}"; do
    # Limpar estado 'failed' se existir
    sudo systemctl reset-failed "${SVC}.service" 2>/dev/null || true
    # Garantir timer activo
    if systemctl is-enabled "${SVC}.timer" &>/dev/null; then
        sudo systemctl restart "${SVC}.timer"
        ok "${SVC}.timer reiniciado"
    else
        sudo systemctl enable --now "${SVC}.timer"
        ok "${SVC}.timer activado"
    fi
done

# ─── Passo 3: validação ─────────────────────────────────────────────────────
echo ""
echo "=== VALIDAÇÃO: a disparar manualmente fs-update_news ==="
sudo systemctl start fs-update_news.service
STATUS=$(systemctl is-active fs-update_news.service 2>/dev/null || true)
RESULT=$(systemctl show fs-update_news.service --property=Result --value 2>/dev/null || echo "unknown")

if [ "${RESULT}" = "success" ]; then
    ok "fs-update_news.service completou com sucesso"
else
    err "fs-update_news.service terminou com resultado: ${RESULT}"
    echo "--- últimas 20 linhas do journal ---"
    journalctl -u fs-update_news.service -n 20 --no-pager
fi

echo ""
echo "=== ESTADO FINAL ==="
systemctl list-timers "${SERVICES[@]/%/.timer}" --no-pager 2>/dev/null || \
    systemctl list-timers --all | grep fs-update

echo ""
echo "--- Units em falha ---"
systemctl --failed --no-pager | grep -E "fs-update|fundscope" || ok "Nenhuma unit em falha"
