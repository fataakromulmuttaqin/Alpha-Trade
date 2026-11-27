@echo off
title ALPHA-TRADE + MetaTrader 5 (EDGE-1H)
cd /d "%~dp0"

set "PY311=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not exist "%PY311%" set "PY311=python"
set "MT5_EXE=C:\Program Files\MetaTrader 5\terminal64.exe"
set "PORT=8788"

echo ==============================================================
echo   ALPHA-TRADE + MetaTrader 5  ^|  XAUUSD (1H / 4H - EDGE-1H)
echo ==============================================================

:: 1) Jika server Alpha-Trade sudah berjalan di port 8788, langsung buka dashboard
netstat -ano | findstr ":%PORT% " | findstr "LISTENING" >nul 2>&1
if %errorlevel%==0 (
  echo [info] Server Alpha-Trade sudah aktif di http://localhost:%PORT%
  start "" "http://localhost:%PORT%"
  timeout /t 3 >nul
  exit /b 0
)

:: 2) Pastikan MetaTrader 5 terbuka; jika belum, buka otomatis & tunggu siap
tasklist /FI "IMAGENAME eq terminal64.exe" 2>nul | find /I "terminal64.exe" >nul
if not %errorlevel%==0 (
  if exist "%MT5_EXE%" (
    echo [mt5] Membuka MetaTrader 5 otomatis...
    start "" "%MT5_EXE%"
    echo [mt5] Menunggu terminal MT5 siap (8 detik)...
    timeout /t 8 /nobreak >nul
  ) else (
    echo [peringatan] terminal64.exe tidak ditemukan di "%MT5_EXE%"
  )
) else (
  echo [mt5] MetaTrader 5 sudah berjalan.
)

:: 3) Jalankan Alpha-Trade + muat semua setting (MT5/MCP/Telegram) + auto-arm DEMO + buka browser
echo [run] Menjalankan Alpha-Trade di http://localhost:%PORT% ...
"%PY311%" -m backend.run ^
  --provider mt5 ^
  --mt5-symbol XAUUSD ^
  --mt5-path "%MT5_EXE%" ^
  --label XAUUSD ^
  --entry-tf 1h ^
  --htf 4h ^
  --sensitivity edge1h ^
  --exit engine ^
  --port %PORT% ^
  --mt5-auto-arm ^
  --open-browser

echo.
echo Server berhenti. Tekan tombol apa saja untuk menutup jendela ini.
pause
