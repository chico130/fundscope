@echo off
chcp 65001 >nul
echo =========================================
echo  FUNDSCOPE — COMMIT, PUSH E CONTEXTO
echo =========================================

REM === NAVEGAR PARA RAIZ DO PROJECTO (um nivel acima de scripts/) ===
cd /d "%~dp0.."

REM === VERIFICAR QUE ESTAMOS NO SITIO CERTO ===
if not exist ".git" (
    echo [ERRO] Repositorio git nao encontrado.
    echo Certifica-te que este .bat esta em fundscope\scripts\
    pause
    exit /b 1
)
echo [OK] Repositorio: %CD%

REM === ACTUALIZAR CLAUDE.md COM CONTEXTO ===
echo A actualizar CLAUDE.md...
for /f "tokens=*" %%i in ('powershell -Command "Get-Date -Format \"yyyy-MM-dd HH:mm\""') do set "NOW=%%i"
for /f "tokens=*" %%i in ('git log -1 --pretty^="%%h - %%s" 2^>nul') do set "LASTCOMMIT=%%i"

powershell -NoProfile -Command ^
  "$file = 'CLAUDE.md';" ^
  "$content = Get-Content $file -Raw -ErrorAction SilentlyContinue;" ^
  "if (-not $content) { $content = '' };" ^
  "$block = \"`n---`n## Auto-Sync: %NOW%`n- PC: %COMPUTERNAME%`n- Ultimo commit: %LASTCOMMIT%`n- Learner: verificar data/beta/ para runs recentes`n---\";" ^
  "if ($content -match '## Auto-Sync:') { $content = $content -replace '(?s)---[\r\n]+## Auto-Sync:.*?---', $block.Trim() } else { $content = $content + $block };" ^
  "Set-Content $file $content -NoNewline -Encoding UTF8;"

echo [OK] CLAUDE.md actualizado (%NOW%)

REM === GIT ADD ===
git add .
git rm --cached fundscope 2>nul

REM === MENSAGEM DO COMMIT ===
echo.
set /p msg="Mensagem do commit (Enter = usa data): "
if "%msg%"=="" set "msg=sync %NOW%"

REM === COMMIT ===
git diff --cached --quiet
if %ERRORLEVEL%==0 (
    echo [INFO] Nenhuma alteracao para commitar.
    goto PULL
)
git commit -m "%msg%"

:PULL
echo A sincronizar com GitHub (pull --rebase)...
git pull --rebase --autostash origin main
if %ERRORLEVEL% neq 0 (
    echo [ERRO] Pull/rebase falhou. Resolve os conflitos manualmente e volta a correr.
    pause
    exit /b 1
)
echo [OK] Rebase concluido.

:PUSH
echo A enviar para GitHub...
git push origin main
if %ERRORLEVEL%==0 (
    echo.
    echo =========================================
    echo  SUCESSO! GitHub actualizado
    echo  Hora: %NOW% - PC: %COMPUTERNAME%
    echo  CLAUDE.md sincronizado
    echo =========================================
) else (
    echo [AVISO] Push falhou mesmo apos pull. Verifica conflitos.
)
pause
