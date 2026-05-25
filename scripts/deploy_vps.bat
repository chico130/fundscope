@echo off
chcp 65001 >nul
echo =========================================
echo  FUNDSCOPE — DEPLOY PARA VPS
echo =========================================

REM === NAVEGAR PARA RAIZ DO PROJECTO ===
if exist "C:\Users\arauj\OneDrive\Ambiente de Trabalho\Fundscope" (
    cd /d "C:\Users\arauj\OneDrive\Ambiente de Trabalho\Fundscope"
) else if exist "C:\Users\Francisco Araujo\Desktop\fundscope" (
    cd /d "C:\Users\Francisco Araujo\Desktop\fundscope"
) else (
    echo [ERRO] Pasta do projecto nao encontrada.
    pause
    exit /b 1
)
echo [OK] Repositorio: %CD%

REM === COMMIT LOCAL (se houver alteracoes) ===
git add .
git diff --cached --quiet
if %ERRORLEVEL% neq 0 (
    set /p msg="Mensagem do deploy: "
    if "%msg%"=="" set "msg=deploy"
    git commit -m "%msg%"
)

REM === PULL ANTES DE PUSH ===
git pull --rebase --autostash origin main
if %ERRORLEVEL% neq 0 (
    echo [ERRO] Pull/rebase local falhou. Resolve conflitos e volta a correr.
    pause
    exit /b 1
)

REM === PUSH ===
git push origin main
if %ERRORLEVEL% neq 0 (
    echo [ERRO] Push falhou.
    pause
    exit /b 1
)

echo =========================================
echo  A fazer pull na VPS...
echo =========================================

REM === Detectar chave SSH (tenta os dois caminhos possiveis) ===
set "SSH_KEY="
if exist "C:\Users\arauj\.ssh\fundscope.pem"             set "SSH_KEY=C:\Users\arauj\.ssh\fundscope.pem"
if exist "C:\Users\Francisco Araujo\.ssh\fundscope.pem" set "SSH_KEY=C:\Users\Francisco Araujo\.ssh\fundscope.pem"

if "%SSH_KEY%"=="" (
    echo [AVISO] Chave SSH nao encontrada. A saltar deploy VPS.
    echo  Coloca fundscope.pem em: C:\Users\arauj\.ssh\
    goto FIM
)
echo [OK] Chave SSH: %SSH_KEY%

REM === RESET --HARD NA VPS (resolve conflitos a favor do remoto) ===
ssh -i "%SSH_KEY%" -o StrictHostKeyChecking=no -o ConnectTimeout=15 ubuntu@134.98.141.58 ^
    "cd ~/fundscope && git fetch origin && git reset --hard origin/main && echo '[VPS] Reset OK'"

if %ERRORLEVEL%==0 (
    echo [OK] VPS sincronizada com origin/main
) else (
    echo [AVISO] SSH falhou. VPS pode nao estar actualizada.
)

:FIM
echo.
echo =========================================
echo  SUCESSO: GitHub actualizado
echo  VPS: reset --hard para origin/main
echo =========================================
pause
