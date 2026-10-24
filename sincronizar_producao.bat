@echo off
chcp 65001 >nul
title Ripfire - pedidos pagos para a producao
cd /d "%~dp0"
python sync_producao.py
pause
