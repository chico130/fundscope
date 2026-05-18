@echo off
setlocal enabledelayedexpansion

:: ============================================================
:: FundScope - Configuracao do Task Scheduler
:: Executar como Administrador (o script faz auto-elevacao)
:: ============================================================

:: Verificar se ja tem privilegios de admin
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo A solicitar elevacao de administrador...
    powershell -Command "Start-Process cmd -ArgumentList '/c \"\"%~f0\"\"' -Verb RunAs -Wait"
    exit /b
)

set "DIR=%~dp0"
:: Remover barra final
if "%DIR:~-1%"=="\" set "DIR=%DIR:~0,-1%"

set "BONNIE=%DIR%\Ligar_Bonnie.bat"
set "BOT=%DIR%\Ligar_Bot.bat"
set "EARNINGS_PY=%DIR%\update_earnings.py"

echo.
echo ============================================================
echo  FundScope - Task Scheduler Setup
echo ============================================================
echo.
echo Pasta do projecto: %DIR%
echo Utilizador:        %USERDOMAIN%\%USERNAME%  (sessao interactiva)
echo.
echo As tarefas correrao SOMENTE com a sessao iniciada (sem password).
echo.

:: ---- Apagar tarefas antigas (ignorar erro se nao existem) ----
schtasks /delete /tn "FundScope_Bonnie_Hourly"   /f >nul 2>&1
schtasks /delete /tn "FundScope_Bot_Daily"       /f >nul 2>&1
schtasks /delete /tn "FundScope_Earnings_Daily"  /f >nul 2>&1

:: ---- Tarefa 1: Ligar_Bonnie.bat - a cada hora ----
echo [1/3] A criar tarefa Bonnie (a cada hora)...
schtasks /create ^
  /tn "FundScope_Bonnie_Hourly" ^
  /tr "cmd /c \"%BONNIE%\"" ^
  /sc HOURLY ^
  /mo 1 ^
  /it ^
  /rl HIGHEST ^
  /f

if %errorLevel%==0 (
    echo     OK - FundScope_Bonnie_Hourly criada com sucesso.
) else (
    echo     ERRO ao criar FundScope_Bonnie_Hourly ^(codigo %errorLevel%^)
)

:: ---- Tarefa 2: Ligar_Bot.bat - diario 08:50, termina 17:10 ----
echo.
echo [2/3] A criar tarefa Bot (08:50 - 17:10 todos os dias)...
schtasks /create ^
  /tn "FundScope_Bot_Daily" ^
  /tr "cmd /c \"%BOT%\"" ^
  /sc DAILY ^
  /st 08:50 ^
  /et 17:10 ^
  /k ^
  /it ^
  /rl HIGHEST ^
  /f

if %errorLevel%==0 (
    echo     OK - FundScope_Bot_Daily criada com sucesso.
) else (
    echo     ERRO ao criar FundScope_Bot_Daily ^(codigo %errorLevel%^)
)

:: ---- Tarefa 3: update_earnings.py - diario 07:00 ----
echo.
echo [3/3] A criar tarefa Earnings (07:00 todos os dias)...
schtasks /create ^
  /tn "FundScope_Earnings_Daily" ^
  /tr "cmd /c \"py \"%EARNINGS_PY%\"\"" ^
  /sc DAILY ^
  /st 07:00 ^
  /it ^
  /rl HIGHEST ^
  /f

if %errorLevel%==0 (
    echo     OK - FundScope_Earnings_Daily criada com sucesso.
) else (
    echo     ERRO ao criar FundScope_Earnings_Daily ^(codigo %errorLevel%^)
)

:: ---- Mostrar tarefas criadas ----
echo.
echo ============================================================
echo  Tarefas agendadas activas:
echo ============================================================
schtasks /query /tn "FundScope_Bonnie_Hourly"  /fo LIST 2>nul
schtasks /query /tn "FundScope_Bot_Daily"      /fo LIST 2>nul
schtasks /query /tn "FundScope_Earnings_Daily" /fo LIST 2>nul

echo.
pause
