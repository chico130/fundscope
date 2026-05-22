@echo off
echo =========================================
echo FAZENDO DEPLOY DIRETO NA VPS...
echo =========================================
cd "C:\Users\arauj\OneDrive\Ambiente de Trabalho\Fundscope"
git add .
set /p msg="Introduz a mensagem do deploy: "
git commit -m "%msg%"
git push vps main
echo =========================================
echo SUCESSO: Codigo atualizado na VPS!
echo =========================================
pause