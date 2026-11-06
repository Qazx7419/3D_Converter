from __future__ import annotations

import argparse
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
MAIN_FILE = PROJECT_DIR / "main.py"
VERSION_FILE = PROJECT_DIR / "pubspec.yaml"
APP_NAME = "3D Converter"


def read_project_version() -> str:
    """Возвращает версию из pubspec.yaml, не требуя стороннего YAML-пакета."""
    if not VERSION_FILE.is_file():
        raise FileNotFoundError(
            f"Не найден {VERSION_FILE.name}. Файл должен лежать рядом с build.py."
        )

    content = VERSION_FILE.read_text(encoding="utf-8-sig")
    match = re.search(r"(?m)^\s*version\s*:\s*([^\s#]+)", content)
    if not match:
        raise ValueError("В pubspec.yaml не найдена строка version: ...")

    return match.group(1).strip().strip("\"'")


def ensure_package(module_name: str, package_name: str) -> None:
    """Устанавливает пакет в используемое Python-окружение, если его нет."""
    if importlib.util.find_spec(module_name) is not None:
        return
    print(f"Пакет {package_name} не найден. Устанавливаю...")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", package_name],
        cwd=PROJECT_DIR,
        check=True,
    )


def ensure_build_dependencies() -> None:
    ensure_package("PyInstaller", "pyinstaller")
    # Нужен для приостановки и возобновления дочернего процесса FFmpeg.
    ensure_package("psutil", "psutil")
    # Нативный drag-and-drop из Проводника Windows.
    ensure_package("tkinterdnd2", "tkinterdnd2==0.6.4")


def build(onefile: bool = False, console: bool = False) -> int:
    if not MAIN_FILE.is_file():
        print("ОШИБКА: main.py должен лежать рядом с build.py.")
        return 2

    try:
        version = read_project_version()
    except (OSError, ValueError) as error:
        print(f"ОШИБКА: {error}")
        return 2

    try:
        ensure_build_dependencies()
    except subprocess.CalledProcessError as error:
        print(f"Не удалось установить PyInstaller (код {error.returncode}).")
        return error.returncode or 1

    # Включаем pubspec.yaml внутрь ресурсов EXE. В main.py функция
    # load_project_version() найдёт его через sys._MEIPASS.
    add_data = f"{VERSION_FILE}{os.pathsep}."
    (PROJECT_DIR / "build").mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name",
        APP_NAME,
        "--collect-all",
        "customtkinter",
        "--collect-all",
        "tkinterdnd2",
        "--add-data",
        add_data,
        "--distpath",
        str(PROJECT_DIR / "dist"),
        "--workpath",
        str(PROJECT_DIR / "build" / "work"),
        "--specpath",
        str(PROJECT_DIR / "build"),
    ]

    command.append("--onefile" if onefile else "--onedir")
    command.append("--console" if console else "--windowed")
    command.append(str(MAIN_FILE))

    print(f"Сборка {APP_NAME} v{version}...")
    print("pubspec.yaml будет включён в ресурсы приложения.")
    print("psutil включён для управления паузой и остановкой FFmpeg.")
    print("tkinterdnd2 включён для перетаскивания видео в окно приложения.")
    print("Команда сборки:")
    print(" ".join(f'"{part}"' if " " in part else part for part in command))

    try:
        subprocess.run(command, cwd=PROJECT_DIR, check=True)
    except subprocess.CalledProcessError as error:
        print(f"Сборка завершилась с ошибкой (код {error.returncode}).")
        return error.returncode or 1

    if onefile:
        exe_path = PROJECT_DIR / "dist" / f"{APP_NAME}.exe"
    else:
        exe_path = PROJECT_DIR / "dist" / APP_NAME / f"{APP_NAME}.exe"

    print("\nСборка завершена.")
    print(f"EXE: {exe_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Сборка 3D Converter с включением pubspec.yaml."
    )
    parser.add_argument(
        "--onefile",
        action="store_true",
        help="создать один EXE-файл вместо рекомендуемой папки приложения",
    )
    parser.add_argument(
        "--console",
        action="store_true",
        help="показывать консольное окно для диагностики",
    )
    args = parser.parse_args()
    return build(onefile=args.onefile, console=args.console)


if __name__ == "__main__":
    raise SystemExit(main())
