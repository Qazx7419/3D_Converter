## 1.0.25
- Replaced the native success dialog with a themed dark CustomTkinter result window.
- Added blue status styling, a clear completion indicator, and a compact output-path card.
- The main application window is dimmed while the modal result dialog is open and restored on close.
- Applied the same themed result dialog to user-stopped conversions; error dialogs remain distinct.

# Changelog

## 1.0.24
- Выпадающий список «Масштабирование» перенесён в верхнюю строку панели «Параметры конвертации», справа от заголовка и непосредственно над колонкой «Аудиодорожки».
- При широком окне масштабирование выравнивается по ширине и положению последней колонки; при узком окне шапка перестраивается вместе с двухколоночной сеткой.
- Нижняя отдельная строка масштабирования удалена; параметры конвертации и строка управления конвертацией сохранены.
- Логика выбора «Авто (GPU)», «GPU» и «CPU», сохранение настройки и аппаратные профили не изменялись.

## 1.0.23
- Заменены вкладки «Конвертация / Масштабирование» на единую панель настроек.
- Добавлен выпадающий список масштабирования: «Авто (GPU)», «GPU», «CPU».
- В автоматическом режиме аппаратные CUDA/VPP-фильтры используются при наличии; иначе применяется CPU Lanczos. Ручной режим GPU сообщает об отсутствии обязательных фильтров вместо скрытого перехода на CPU.
- Добавлены справки при наведении на «Масштабирование» и «Тестовый фрагмент».
- Тестовый фрагмент принимает одно число минут, диапазон минут (например `10 - 125`) или временной диапазон (`0:23:53 - 1:15:01`). В команде FFmpeg задаются начальная позиция `-ss` и продолжительность `-t`.
- Скрыты консольные окна дочерних ffprobe/ffmpeg-процессов и проверки фильтров через `CREATE_NO_WINDOW`.

# История изменений 3D Converter

## v1.0.22 — Scaling selection tab

- Added a dedicated **Масштабирование** tab beside conversion settings.
- Default mode is **Автоматически**: use NVIDIA CUDA or Intel VPP hardware scaling when the required FFmpeg filters are available.
- Added an explicit **Программное** mode that forces CPU Lanczos scaling while keeping the selected hardware encoder where compatible.
- Automatic mode falls back to software Lanczos when the required hardware scaling/composition filters are unavailable.
- Scaling preference is saved in the existing per-user application settings alongside window geometry.
- Added command-generation tests for automatic hardware selection, forced software scaling, and missing-filter fallback.

## v1.0.21 — Аппаратная обработка Half TAB → Half SBS

- NVIDIA NVENC: добавлена CUDA-цепочка для Half TAB → Half SBS: аппаратное кадрирование, масштабирование обеих половин через `scale_cuda` с Lanczos и сборка через `overlay_cuda`.
- Intel QSV: добавлена аппаратная обработка обеих половин через `vpp_qsv` и объединение через `hstack_qsv`.
- Видеокадры в аппаратных вариантах остаются в памяти GPU/QSV; программная цепочка split/crop/scale/hstack больше не используется, если нужные фильтры доступны.
- Добавлены проверки наличия `scale_cuda`, `overlay_cuda`, `vpp_qsv` и `hstack_qsv`. При отсутствии фильтра остаётся прежний совместимый программный путь.
- Обновлён эталонный BAT Top Half OU → Half SBS: аппаратная CUDA-обработка с автоматическим программным fallback.
- Обновлены тесты генерации команд и документация.


## v1.0.20 — Аппаратное масштабирование NVIDIA CUDA

- Для операции Full SBS → Half SBS профиль NVIDIA переведён на CUDA-цепочку: `-hwaccel cuda -hwaccel_output_format cuda` → `scale_cuda` → `h264_nvenc`.
- Масштабирование выполняется на GPU с алгоритмом Lanczos и выходным форматом NV12.
- Перед сборкой команды проверяется наличие `scale_cuda` в используемом FFmpeg; если фильтр отсутствует, автоматически сохраняется прежний совместимый программный Lanczos-путь.
- Устранён промежуточный возврат кадров в системную память перед масштабированием.
- Для Half TAB → Half SBS сохранён проверенный прежний граф split/crop/scale/hstack; он не переводился на CUDA в рамках этого изменения.
- Обновлены тесты генератора команд, README и версия проекта.

## v1.0.18 — Drag & Drop

- Добавлена загрузка видео перетаскиванием файла из Проводника Windows на окно приложения.
- Приём файлов включён на всех основных областях окна.
- Список и анализ загружаемого файла используют общий обработчик с обычным диалогом выбора.
- Во время конвертации загрузка другого файла блокируется.
- При перетаскивании нескольких файлов программа сообщает, что в текущей версии можно загружать по одному видео за раз.
- В `build.py` добавлена установка `tkinterdnd2` и включение пакета с ресурсами tkdnd в PyInstaller.
- Обновлён `requirements.txt`, README и PyInstaller spec.

## v1.0.17 — Стабильная базовая версия

Эта версия зафиксирована как стабильная база для последующих изменений. Функциональность v1.0.17 сохранена; drag-and-drop вынесен в отдельное обновление v1.0.18.
