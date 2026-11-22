@echo off
REM Re-tune bergulir EDGE — jalankan tiap KUARTAL (Jan/Apr/Jul/Okt).
REM Hasil: cache/edge_tune_*.json -> bandingkan champion lama (TRAIN pick),
REM verifikasi OOS sekali, baru perbarui preset edge1h di godmode_engine.
cd /d "%~dp0.."
set PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe
echo [1/2] tuning 1h (120 trial)...
%PY% -m backend.tune_edge --tf 1h --trials 120 --mode mean
echo [2/2] tuning 15m (80 trial)...
%PY% -m backend.tune_edge --tf 15m --trials 80 --mode mean
echo Selesai. Simpan hasil: python -m backend.tune_edge --report
pause
