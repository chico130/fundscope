@echo off
echo =========================================
echo FAZENDO DEPLOY DIRETO NA VPS...
echo =========================================
if exist "C:\Users\arauj\OneDrive\Ambiente de Trabalho\Fundscope" (
    cd "C:\Users\arauj\OneDrive\Ambiente de Trabalho\Fundscope"
) else (
    cd "C:\Users\Francisco Araujo\Desktop\fundscope"
)
git add .
set /p msg="Introduz a mensagem do deploy: "
git commit -m "%msg%"
git push vps main
echo =========================================
echo SUCESSO: Codigo atualizado na VPS!
echo =========================================
pause