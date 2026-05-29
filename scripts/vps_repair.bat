@echo off
title FundScope VPS — Repair ingest services
echo A conectar ao VPS e a correr o repair...
ssh -i "%USERPROFILE%\Downloads\ssh-key-2026-05-19.key" ubuntu@134.98.141.58 "cd ~/fundscope && git pull --ff-only && bash scripts/vps_repair.sh"
@cmd /k
