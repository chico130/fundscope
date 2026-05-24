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
git push origin main
echo =========================================
echo A fazer pull na VPS...
echo =========================================
ssh -i "C:\Users\Francisco Araujo\.ssh\fundscope.pem" ubuntu@134.98.141.58 "cd ~/fundscope && git stash && git pull origin main && git stash pop"
echo =========================================
echo SUCESSO: GitHub e VPS atualizados!
echo =========================================
pause