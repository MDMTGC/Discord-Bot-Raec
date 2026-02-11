@echo off
title RAEC v3 ACE - Sigil of the Dying Star
color 0B
echo.
echo ====================================================
echo    RAEC v3 ACE - Sigil of the Dying Star
echo ====================================================
echo.
echo Starting RAEC...
echo.

python Raec_v3_ACE.py

if errorlevel 1 (
    echo.
    echo ====================================================
    echo ERROR: Bot crashed or failed to start
    echo ====================================================
    echo.
    pause
) else (
    echo.
    echo ====================================================
    echo Bot stopped normally
    echo ====================================================
    echo.
    pause
)
