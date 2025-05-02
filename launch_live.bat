@echo off
title Momentum-Quant Live
cd /d "%~dp0"
REM activate venv
call .venv\Scripts\activate.bat
REM run the supervisor
python -m scripts.run_live
pause
