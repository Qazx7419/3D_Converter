@echo off
chcp 65001 >nul

if "%~1"=="" (
    echo [!] Ошибка: Перетащите видео на этот файл.
    pause
    exit /b
)

echo ======================================================
echo 📦 МОВИНГ В TS (Копирование видео и аудио без перекодирования)
echo ======================================================
echo.

ffmpeg -i "%~1" -c:v copy -c:a copy "%~dpn1_Remux.ts" -y

echo.
if %ERRORLEVEL% EQU 0 (
    echo [✓] Успешно упаковано в TS: "%~dpn1_Remux.ts"
) else (
    echo [!] Ошибка при перепаковке в TS.
)

pause