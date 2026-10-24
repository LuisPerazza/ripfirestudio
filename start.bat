@echo off
title Ripfire Studio Local
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python nao encontrado. Instale o Python 3.11 ou mais novo e marque Add Python to PATH.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo Criando ambiente virtual...
  python -m venv .venv
)
netstat -ano | findstr /r /c:"127.0.0.1:8787 .*LISTENING" >nul
if not errorlevel 1 (
  echo O Ripfire Studio ja esta rodando. Abrindo o navegador...
  start "" "http://127.0.0.1:8787"
  exit /b 0
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
rem Abre o navegador so quando o servidor ja estiver respondendo.
rem Abrir antes fazia a pagina carregar do cache e dar "Failed to fetch".
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "for($i=0;$i -lt 120;$i++){try{Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 'http://127.0.0.1:8787/api/config' | Out-Null; Start-Process 'http://127.0.0.1:8787'; break}catch{Start-Sleep -Milliseconds 500}}"
".venv\Scripts\python.exe" app.py
pause
