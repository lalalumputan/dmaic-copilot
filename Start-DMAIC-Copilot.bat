@echo off
:: ==========================================
:: DMAIC COPILOT LAUNCHER
:: Jalankan Streamlit dari folder root project
:: ==========================================

:: pindah ke folder tempat file ini berada (root project)
cd /d "%~dp0"

echo.
echo 🚀 Menjalankan DMAIC Copilot...
echo (Tunggu sebentar, browser akan terbuka otomatis)
echo.

:: aktifkan virtual environment jika ada
IF EXIST ".venv\Scripts\activate" (
    call .venv\Scripts\activate
    echo Virtual environment aktif.
) ELSE (
    echo Tidak ada virtual environment. Menggunakan Python global.
)

:: jalankan Streamlit app
streamlit run app\app.py

:: jika ingin port lain (misal 8502) gunakan baris ini:
:: streamlit run app\app.py --server.port 8502

echo.
echo ==============================
echo Aplikasi DMAIC Copilot selesai
echo ==============================
pause
