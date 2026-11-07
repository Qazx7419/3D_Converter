@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
:: Версия 2.9 CUDA: аппаратное масштабирование и сборка Half SBS через GPU.

if "%~1"=="" (
    echo [!] Ошибка: Перетащите видео на этот файл.
    pause
    exit /b
)

where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo [!] Ошибка: FFmpeg не найден в PATH.
    pause
    exit /b 1
)

:: Проверяем поддержку CUDA-масштабирования и CUDA-компоновки.
:: Если один из фильтров отсутствует, ниже будет использован прежний CPU-граф.
set "CUDA_HW_READY=0"
ffmpeg -hide_banner -filters 2>&1 | findstr /i /c:"scale_cuda" >nul
if not errorlevel 1 (
    ffmpeg -hide_banner -filters 2>&1 | findstr /i /c:"overlay_cuda" >nul
    if not errorlevel 1 set "CUDA_HW_READY=1"
)

:MENU_UNIT
cls
echo ======================================================
echo      3D КОНВЕРТЕР: Half OU -> Half SBS (CUDA / NVENC)
echo ======================================================
echo.
echo ВЫБЕРИТЕ РЕЖИМ И ЕДИНИЦЫ ИЗМЕРЕНИЯ:
echo.
echo 0. 🧙‍♂️ ВОЛШЕБНАЯ КНОПКА (Авто-анализ и подбор параметров)
echo ------------------------------------------------------
echo 1. Динамический VBR (в Мбит/с / Mbps)    [Пример: 15]
echo 2. Динамический VBR (в МБ/с / MB/s)      [Пример: 2]
echo 3. Динамический VBR (по РАЗМЕРУ файла)   [Пример: 4500 МБ]
echo 4. Авто-битрейт по качеству CQ          [Динамический, без рамок]
echo 5. ПОСТОЯННЫЙ БИТРЕЙТ (CBR в Мбит/с)     [Фиксированная скорость]
echo.
set "unit_choice="
set /p unit_choice="Ваш выбор (0-5) [по умолчанию 0]: "
if not defined unit_choice set "unit_choice=0"

if "!unit_choice!"=="0" goto AUTO_MAGIC
if "!unit_choice!"=="1" goto SET_MBPS
if "!unit_choice!"=="2" goto SET_MBYS
if "!unit_choice!"=="3" goto SET_FILESIZE
if "!unit_choice!"=="4" goto SET_CQ
if "!unit_choice!"=="5" goto SET_CBR
goto MENU_UNIT

:AUTO_MAGIC
echo.
echo ======================================================
echo 🔍 СКАНЕР: Анализируем исходный видеофайл...
echo ======================================================

set "v_height=1080"
set "v_orig_bitrate=10000000"

for /f "tokens=*" %%a in ('ffprobe -v error -select_streams v:0 -show_entries stream^=height -of default^=noprint_wrappers^=1:nokey^=1 "%~1" 2^>nul') do (
    set "v_height=%%a"
)

for /f "tokens=*" %%a in ('ffprobe -v error -show_entries format^=bit_rate -of default^=noprint_wrappers^=1:nokey^=1 "%~1" 2^>nul') do (
    set "v_orig_bitrate=%%a"
)

set /a orig_mbps=v_orig_bitrate / 1000000

if !v_height! GTR 1080 (
    set "v_cq=19"
    set "v_pre=p6"
    set "auto_reason=Высокое разрешение исходника (выше 1080p)"
) else if !orig_mbps! GEQ 20 (
    set "v_cq=19"
    set "v_pre=p6"
    set "auto_reason=Высокий исходный битрейт (более 20 Mbps)"
) else (
    set "v_cq=21"
    set "v_pre=p5"
    set "auto_reason=Стандартный HD/Full HD исходник"
)

set "encode_params=-rc vbr -cq !v_cq! -b:v 0"
set "info_msg=🧙‍♂️ Авто-режим (!auto_reason!): CQ !v_cq!, Пресет !v_pre!, Динамический VBR"

echo.
echo [✓] Анализ завершён: !auto_reason!
echo [✓] Назначены оптимальные настройки: CQ !v_cq!, Пресет !v_pre!
timeout /t 2 >nul
goto MENU_TEST

:SET_MBPS
echo.
echo Укажите средний битрейт в Мбит/с (Mbps):
set "v_mbps="
set /p v_mbps="Значение в Мбит/с [по умолчанию 15]: "
if not defined v_mbps set "v_mbps=15"

set /a v_bitrate=v_mbps*1000
goto CALC_VBR

:SET_MBYS
echo.
echo Укажите битрейт в Мегабайтах в секунду (МБ/с):
set "v_mbys="
set /p v_mbys="Значение в МБ/с [по умолчанию 2]: "
if not defined v_mbys set "v_mbys=2"

set /a v_bitrate=v_mbys*8*1000
goto CALC_VBR

:SET_FILESIZE
echo.
echo Укажите желаемый ИТОГОВЫЙ РАЗМЕР файла в Мегабайтах (МБ):
set "target_size_mb="
set /p target_size_mb="Размер файла в МБ: "

if not defined target_size_mb goto SET_FILESIZE

echo.
echo Управление длительностью:
echo 1. Рассчитать автоматически из исходного файла
echo 2. Указать длительность вручную (в минутах)
echo.
set /p dur_type="Ваш выбор (1-2): "

if "!dur_type!"=="2" (
    set /p duration_min="Введите длительность видео в минутах: "
    set /a duration_sec=duration_min*60
) else (
    echo Вычисляем длительность видео...
    for /f "tokens=*" %%a in ('ffprobe -v error -show_entries format^=duration -of default^=noprint_wrappers^=1:nokey^=1 "%~1" 2^>nul') do (
        for /f "delims=." %%b in ("%%a") do set "duration_sec=%%b"
    )
)

if not defined duration_sec (
    echo [!] Не удалось определить длительность. Установлена по умолчанию 120 мин.
    set "duration_sec=7200"
)

set /a v_bitrate=(target_size_mb * 8192) / duration_sec
goto CALC_VBR

:CALC_VBR
set /a v_maxrate=v_bitrate + (v_bitrate / 2)
set /a v_bufsize=v_maxrate * 2

set "encode_params=-rc vbr -b:v !v_bitrate!k -maxrate !v_maxrate!k -bufsize !v_bufsize!k"
set "info_msg=Динамический VBR: средний !v_bitrate! kbit/s (пик до !v_maxrate! kbit/s)"
goto PRESET_SELECT

:SET_CBR
echo.
echo Укажите ПОСТОЯННЫЙ битрейт (CBR) в Мбит/с (Mbps):
set "v_cbr_mbps="
set /p v_cbr_mbps="Битрейт в Мбит/с [по умолчанию 15]: "
if not defined v_cbr_mbps set "v_cbr_mbps=15"

set /a v_bitrate=v_cbr_mbps*1000

set "encode_params=-rc cbr -b:v !v_bitrate!k -maxrate !v_bitrate!k -minrate !v_bitrate!k -bufsize !v_bitrate!k"
set "info_msg=ПОСТОЯННЫЙ битрейт (CBR): строго !v_bitrate! kbit/s"
goto PRESET_SELECT

:SET_CQ
echo.
set "v_cq="
set /p v_cq="Значение CQ (15-28) [по умолчанию 21]: "
if not defined v_cq set "v_cq=21"

set "encode_params=-rc vbr -cq !v_cq! -b:v 0"
set "info_msg=Режим CQ (Авто-битрейт по качеству): !v_cq!"
goto PRESET_SELECT

:PRESET_SELECT
echo.
echo ======================================================
echo ВЫБОР ПРЕСЕТА СКОРОСТИ И КАЧЕСТВА (NVENC):
echo p1 - p3 : Быстро / Низкое качество
echo p4 - p5 : Оптимально (Баланс)
echo p6 - p7 : Медленно / Макс. качество
echo.
set "user_pre="
set /p user_pre="Пресет p1-p7 [Enter = Авто]: "

if not defined user_pre (
    set "v_pre=p5"
) else (
    set "v_pre=p!user_pre!"
)

:MENU_TEST
echo.
echo ------------------------------------------------------
echo 1. ПОЛНОЕ ВИДЕО
echo 2. ТЕСТОВЫЙ ФРАГМЕНТ
echo ------------------------------------------------------
echo.
set "dur_mode="
set /p dur_mode="Ваш выбор (1-2) [по умолчанию 1]: "
if not defined dur_mode set "dur_mode=1"

if "!dur_mode!"=="2" goto SET_TEST_TIME
set "time_limit="
goto MENU_FORMAT

:SET_TEST_TIME
echo.
echo Укажите длительность тестового фрагмента в минутах:
set "test_min="
set /p test_min="Длительность в минутах [по умолчанию 2]: "
if not defined test_min set "test_min=2"

set /a test_sec=test_min*60
set "time_limit=-t !test_sec!"
goto MENU_FORMAT

:MENU_FORMAT
echo.
echo ------------------------------------------------------
echo ВЫБЕРИТЕ ФОРМАТ КОНТЕЙНЕРА:
echo 1. MP4 (.mp4)    - Стандартный контейнер (с faststart)
echo 2. MPEG-TS (.ts) - Потоковый контейнер для Smart TV / DLNA
echo ------------------------------------------------------
echo.
set "fmt_choice="
set /p fmt_choice="Ваш выбор (1-2) [по умолчанию 2]: "
if not defined fmt_choice set "fmt_choice=2"

if "!fmt_choice!"=="1" (
    set "ext=mp4"
    set "container_flags=-brand mp42 -movflags +faststart"
    set "sub_codec=-c:s mov_text"
    set "format_name=MP4 (.mp4)"
) else (
    set "ext=ts"
    set "container_flags="
    set "sub_codec=-c:s copy"
    set "format_name=MPEG-TS (.ts)"
)
goto MENU_AUDIO

:MENU_AUDIO
echo.
echo ------------------------------------------------------
echo НАСТРОЙКА АУДИОДОРОЖЕК:
echo 1. Конвертировать ВСЕ аудиодорожки в AC3 (448k) [Рекомендуется]
echo 2. Копировать ВСЕ аудиодорожки (без изменений)   [Быстро]
echo ------------------------------------------------------
echo.
set "audio_choice="
set /p audio_choice="Ваш выбор (1-2) [по умолчанию 1]: "
if not defined audio_choice set "audio_choice=1"

if "!audio_choice!"=="2" (
    set "audio_params=-c:a copy"
    set "audio_name=Исходный звук (копирование всех дорожек)"
) else (
    set "audio_params=-c:a ac3 -b:a 448k"
    set "audio_name=AC3 (448 kbps) для всех дорожек"
)
goto RUN_ENCODE

:RUN_ENCODE
cls
echo ======================================================
echo 🚀 ЗАПУСК КОДИРОВАНИЯ (Преобразование Half OU -> Half SBS)
echo !info_msg!
echo Пресет: !v_pre!
echo Формат контейнера: !format_name!
echo Аудиодорожки: !audio_name!
echo ======================================================
echo.

if "!CUDA_HW_READY!"=="1" goto RUN_CUDA

goto RUN_SOFTWARE

:RUN_CUDA
echo Масштабирование: NVIDIA CUDA (scale_cuda Lanczos)
echo Декодирование: NVDEC / CUDA
echo Компоновка двух видов: overlay_cuda
echo Кодирование: NVIDIA NVENC
echo.
ffmpeg -hwaccel cuda -hwaccel_output_format cuda -i "%~1" ^
-filter_complex "[0:v]split=3[base_src][top_src][bottom_src];[base_src]scale_cuda=1920:1080:format=nv12[canvas];[top_src]crop=iw:ih/2:0:0,scale_cuda=960:1080:interp_algo=lanczos:format=nv12[top_eye];[bottom_src]crop=iw:ih/2:0:ih/2,scale_cuda=960:1080:interp_algo=lanczos:format=nv12[bottom_eye];[canvas][top_eye]overlay_cuda=x=0:y=0[tmp];[tmp][bottom_eye]overlay_cuda=x=960:y=0[vout]" ^
-map "[vout]" -map 0:a? -map 0:s? ^
-c:v h264_nvenc -preset !v_pre! -tune hq !encode_params! ^
-profile:v high -level 4.1 -aspect 16:9 ^
-metadata:s:v:0 stereo_mode="left_right" ^
!audio_params! !sub_codec! !container_flags! !time_limit! ^
"%~dpn1_HalfSBS.!ext!" -y
set "FFMPEG_ERROR=!errorlevel!"
goto ENCODE_RESULT

:RUN_SOFTWARE
echo Масштабирование: программный Lanczos (CUDA-фильтры недоступны)
echo Декодирование: NVDEC
echo Совместимый резервный режим; аппаратное кодирование NVENC.
echo.
ffmpeg -hwaccel nvdec -i "%~1" ^
-map 0:v:0 -map 0:a? -map 0:s? ^
-vf "split[top][bottom]; [top]crop=iw:ih/2:0:0,scale=960:1080:flags=lanczos[left]; [bottom]crop=iw:ih/2:0:ih/2,scale=960:1080:flags=lanczos[right]; [left][right]hstack,setsar=1:1,format=yuv420p" ^
-c:v h264_nvenc -preset !v_pre! -tune hq !encode_params! ^
-profile:v high -level 4.1 -aspect 16:9 ^
-metadata:s:v:0 stereo_mode="left_right" ^
!audio_params! !sub_codec! !container_flags! !time_limit! ^
"%~dpn1_HalfSBS.!ext!" -y
set "FFMPEG_ERROR=!errorlevel!"

:ENCODE_RESULT
echo.
if "!FFMPEG_ERROR!"=="0" goto ENCODE_SUCCESS
goto ENCODE_FAILED

:ENCODE_SUCCESS
echo ========================================
echo ГОТОВО!
echo Файл: "%~dpn1_HalfSBS.!ext!"
echo ========================================
goto ENCODE_FINISH

:ENCODE_FAILED
echo [!] Ошибка кодирования FFmpeg. Код ошибки: !FFMPEG_ERROR!
echo Проверьте сообщения FFmpeg выше.

:ENCODE_FINISH
echo.
echo Нажмите любую клавишу, чтобы закрыть окно.
pause >nul
