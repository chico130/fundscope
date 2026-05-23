@echo off
echo =========================================
echo ENVIANDO ATUALIZACOES PARA O GITHUB...
echo =========================================
if exist "C:\Users\arauj\OneDrive\Ambiente de Trabalho\Fundscope" (
    cd "C:\Users\arauj\OneDrive\Ambiente de Trabalho\Fundscope"
) else (
    cd "C:\Users\Francisco Araujo\Desktop\fundscope"
)
git add .
set /p msg="Introduz a mensagem do commit: "
git commit -m "%msg%"
git push origin main
echo =========================================
echo SUCESSO: Sincronizado com o GitHub!
echo =========================================
pause