@echo off
chcp 65001 >nul
title Ripfire - instalar o PC servidor
rem Prepara este PC para receber os pedidos pagos do site:
rem   1. instala o Python (se faltar)
rem   2. copia o programa para C:\Ripfire
rem   3. cria a pasta C:\Pedidos Ripfire e compartilha na rede como PedidosRipfire
rem   4. faz o programa abrir sozinho quando o Windows ligar
rem Precisa rodar como administrador (ele pede sozinho).

net session >nul 2>&1
if errorlevel 1 (
  echo Pedindo permissao de administrador...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
cd /d "%~dp0"

echo.
echo === 1/4 Python ===
where python >nul 2>&1 && python --version >nul 2>&1
if errorlevel 1 (
  echo Python nao encontrado. Instalando pelo winget...
  winget install -e --id Python.Python.3.12 --scope machine --accept-package-agreements --accept-source-agreements
  if errorlevel 1 (
    echo.
    echo Nao deu para instalar o Python sozinho.
    echo Baixe em https://www.python.org/downloads/ e marque "Add python.exe to PATH".
    echo Depois rode este instalador de novo.
    pause
    exit /b 1
  )
  echo Python instalado. FECHE esta janela e rode o instalador DE NOVO para continuar.
  pause
  exit /b 0
)
python --version

echo.
echo === 2/4 Programa em C:\Ripfire ===
if not exist "C:\Ripfire" mkdir "C:\Ripfire"
copy /Y "sync_producao.py" "C:\Ripfire\" >nul
copy /Y "sincronizar_producao.bat" "C:\Ripfire\" >nul
echo Copiado.

echo.
echo === 3/4 Pasta dos pedidos e compartilhamento ===
if not exist "C:\Pedidos Ripfire" mkdir "C:\Pedidos Ripfire"
rem S-1-1-0 = "Todos" em qualquer idioma do Windows. Modificar = ler, criar e mudar (sem trocar permissoes).
icacls "C:\Pedidos Ripfire" /grant *S-1-1-0:(OI)(CI)M >nul
powershell -NoProfile -Command ^
  "$todos=(New-Object System.Security.Principal.SecurityIdentifier('S-1-1-0')).Translate([System.Security.Principal.NTAccount]).Value;" ^
  "if(Get-SmbShare -Name 'PedidosRipfire' -ErrorAction SilentlyContinue){'Compartilhamento ja existia.'}" ^
  "else{New-SmbShare -Name 'PedidosRipfire' -Path 'C:\Pedidos Ripfire' -ChangeAccess $todos | Out-Null; 'Compartilhado.'}"
echo Nos PCs das impressoras, abra: \\%COMPUTERNAME%\PedidosRipfire

echo.
echo === 4/4 Abrir sozinho ao ligar o PC ===
powershell -NoProfile -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('CommonStartup')+'\Ripfire pedidos.lnk');" ^
  "$s.TargetPath='C:\Ripfire\sincronizar_producao.bat'; $s.WorkingDirectory='C:\Ripfire'; $s.WindowStyle=7; $s.Save(); 'Pronto.'"

echo.
echo ============================================================
echo  Tudo pronto. Agora vai abrir o programa pela primeira vez.
echo  Ele vai perguntar:
echo    - endereco do site:  https://ripfirestudio.onrender.com
echo    - usuario e senha do login "PC servidor" criado no Admin
echo    - pasta de producao: aperte ENTER (C:\Pedidos Ripfire)
echo ============================================================
pause
start "" "C:\Ripfire\sincronizar_producao.bat"
