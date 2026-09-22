@echo off
title Ripfire Studio Local
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python nao encontrado. Instale Python 3.11 ou 3.12 e marque Add Python to PATH.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo Criando ambiente virtual...
  python -m venv .venv
)
echo Instalando dependencias...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo Falha ao instalar as dependencias. Veja o erro acima.
  echo Se for sobre o Pillow nao ter versao para o seu Python,
  echo confira se o requirements.txt pede "Pillow>=12.0.0".
  pause
  exit /b 1
)
start "" "http://127.0.0.1:8787"
".venv\Scripts\python.exe" app.py
pause
