import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox
import subprocess
import os
import json
import shutil
import re
import sys
import threading
import time
from dataclasses import replace
from io import BytesIO
from PIL import Image

try:
    import psutil
except ImportError:  # pause/resume is enabled in builds made by build.py
    psutil = None

from encoding_profiles import (
    ConversionSettings,
    QualitySettings,
    SourceInfo,
    ProfileError,
    build_conversion_plan,
)
from output_path_utils import ensure_required_extension, next_available_variant
from preset_options import preset_options_for_encoder, recommended_preset
from quality_value_help import build_quality_field_help, apply_quality_field_state


def load_project_version():
    """Читает версию из pubspec.yaml рядом с проектом или в ресурсах EXE."""
    candidates = []

    # При сборке PyInstaller дополнительные данные могут находиться в _MEIPASS.
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir:
        candidates.append(os.path.join(bundle_dir, "pubspec.yaml"))

    # Обычный запуск Python: pubspec.yaml лежит рядом с main.py.
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pubspec.yaml"))

    # Запасные варианты для сборок и нестандартного способа запуска.
    if getattr(sys, "frozen", False):
        candidates.append(os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "pubspec.yaml"))
    candidates.append(os.path.join(os.getcwd(), "pubspec.yaml"))

    for candidate in candidates:
        try:
            with open(candidate, "r", encoding="utf-8-sig") as version_file:
                content = version_file.read()
            match = re.search(r"(?m)^\s*version\s*:\s*([^\s#]+)", content)
            if match:
                version = match.group(1).strip().strip("\"'")
                if version:
                    return version
        except OSError:
            continue

    return "dev"

# Настройки внешнего вида
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.app_version = load_project_version()
        self.title("3D Converter — Автоопределение")
        self.minsize(900, 560)

        # Восстанавливаем геометрию последнего обычного окна. Настройки хранятся
        # отдельно от папки программы, поэтому работают и для будущей EXE-сборки.
        self._window_state_path = self._get_window_state_path()
        saved_window_state = self._load_window_state()
        self._last_normal_geometry = saved_window_state["geometry"]
        self._restore_maximized = saved_window_state["maximized"]
        self.geometry(self._last_normal_geometry)

        self.video_path = None
        self.video_info = {}
        self._conversion_process = None
        self._conversion_state = "idle"  # idle, starting, running, paused, stopping
        self._conversion_started_at = None
        self._pause_started_at = None
        self._total_paused_seconds = 0.0
        self._stop_requested = False
        self._close_when_finished = False
        self._preset_manual = False
        self.hw_info = self.detect_hardware()
        self.preview_ctk_image = None
        self.preview_source_image = None
        self._preview_resize_job = None
        self._preview_last_render_size = None
        self._layout_resize_job = None
        self._last_layout_columns = None
        self._quality_tooltip_job = None
        self._quality_tooltip_window = None

        # Корневой grid: шапка и нижние панели имеют фиксированную высоту,
        # центральная область забирает всё оставшееся место.
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # === Компактная шапка ===
        self.header_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.header_frame.grid(row=0, column=0, sticky="ew", padx=12, pady=(5, 2))
        self.header_frame.grid_columnconfigure(0, weight=1)

        # Группируем заголовок и небольшую подпись версии, чтобы вся надпись
        # оставалась визуально центрированной при любом размере окна.
        self.header_group = ctk.CTkFrame(self.header_frame, fg_color="transparent")
        self.header_group.grid(row=0, column=0, pady=(2, 1))

        self.header = ctk.CTkLabel(
            self.header_group,
            text="3D Video Converter",
            font=ctk.CTkFont(size=23, weight="bold")
        )
        self.header.pack(side="left")

        # Версия оформлена компактным синим бейджем в стиле элементов управления.
        # Нижнее выравнивание совмещает её с основанием строки заголовка.
        self.version_badge = ctk.CTkFrame(
            self.header_group,
            fg_color="#1f6aa5",
            corner_radius=5
        )
        self.version_badge.pack(side="left", anchor="s", padx=(9, 0), pady=(0, 0))

        self.version_label = ctk.CTkLabel(
            self.version_badge,
            text=f"v{self.app_version}",
            font=ctk.CTkFont(size=9, weight="bold"),
            text_color="#ffffff",
            fg_color="transparent",
            height=14  # Высота строки текста; внешний синий бейдж будет на 2 px выше.
        )
        self.version_label.pack(padx=7, pady=(1, 1))

        self.subtitle = ctk.CTkLabel(
            self.header_frame,
            text="Автоматическое определение типа видео и подбор метода конвертации",
            font=ctk.CTkFont(size=12),
            text_color="gray70"
        )
        self.subtitle.grid(row=1, column=0, pady=(0, 3))

        self.select_btn = ctk.CTkButton(
            self.header_frame,
            text="Выбрать видеофайл",
            command=self.select_video,
            height=34,
            font=ctk.CTkFont(size=13)
        )
        self.select_btn.grid(row=2, column=0, pady=(2, 2))

        self.file_label = ctk.CTkLabel(
            self.header_frame,
            text="Файл не выбран",
            font=ctk.CTkFont(size=12),
            text_color="gray60"
        )
        self.file_label.grid(row=3, column=0, pady=(0, 1))

        # === Центральная область: две равные адаптивные колонки ===
        self.content_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.content_frame.grid(row=1, column=0, sticky="nsew", padx=12, pady=(3, 5))
        self.content_frame.grid_columnconfigure(0, weight=1, uniform="main_columns")
        self.content_frame.grid_columnconfigure(1, weight=1, uniform="main_columns")
        self.content_frame.grid_rowconfigure(0, weight=1)

        self.left_frame = ctk.CTkFrame(self.content_frame, fg_color="transparent")
        self.left_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        self.left_frame.grid_columnconfigure(0, weight=1)
        self.left_frame.grid_rowconfigure(0, weight=1, uniform="left_panels")
        self.left_frame.grid_rowconfigure(1, weight=1, uniform="left_panels")

        # === Информация о видео ===
        self.info_frame = ctk.CTkFrame(self.left_frame)
        self.info_frame.grid(row=0, column=0, sticky="nsew", pady=(0, 5))
        self.info_frame.grid_columnconfigure(0, weight=1)
        self.info_frame.grid_rowconfigure(1, weight=1)

        self.info_title = ctk.CTkLabel(
            self.info_frame,
            text="Информация о видео",
            font=ctk.CTkFont(size=14, weight="bold")
        )
        self.info_title.grid(row=0, column=0, pady=(5, 2))

        self.info_text = ctk.CTkTextbox(
            self.info_frame,
            height=1,
            font=ctk.CTkFont(size=12),
            activate_scrollbars=False
        )
        self.info_text.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 7))
        self.info_text.insert("1.0", "Выберите видеофайл для анализа...")
        self.info_text.configure(state="disabled")

        # === Рекомендации ===
        self.rec_frame = ctk.CTkFrame(self.left_frame)
        self.rec_frame.grid(row=1, column=0, sticky="nsew", pady=(5, 0))
        self.rec_frame.grid_columnconfigure(0, weight=1)
        self.rec_frame.grid_rowconfigure(1, weight=1)

        self.rec_title = ctk.CTkLabel(
            self.rec_frame,
            text="Рекомендуемые действия",
            font=ctk.CTkFont(size=14, weight="bold")
        )
        self.rec_title.grid(row=0, column=0, pady=(5, 2))

        self.rec_text = ctk.CTkTextbox(
            self.rec_frame,
            height=1,
            font=ctk.CTkFont(size=12)
        )
        self.rec_text.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 7))
        self.rec_text.insert("1.0", "Здесь появятся рекомендации после анализа...")
        self.rec_text.configure(state="disabled")

        # === Предпросмотр: изображение занимает всё доступное место ===
        self.preview_frame = ctk.CTkFrame(self.content_frame)
        self.preview_frame.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        self.preview_frame.grid_columnconfigure(0, weight=1)
        self.preview_frame.grid_rowconfigure(1, weight=1)

        self.preview_title = ctk.CTkLabel(
            self.preview_frame,
            text="Предпросмотр видео",
            font=ctk.CTkFont(size=14, weight="bold")
        )
        self.preview_title.grid(row=0, column=0, pady=(5, 3))

        self.preview_image_label = ctk.CTkLabel(
            self.preview_frame,
            text="Выберите видеофайл,\nчтобы увидеть кадр",
            width=1,
            height=1,
            fg_color="#171717",
            corner_radius=8,
            font=ctk.CTkFont(size=13),
            text_color="gray65"
        )
        self.preview_image_label.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 4))
        self.preview_image_label.bind("<Configure>", self._on_preview_area_resize)

        self.preview_time_label = ctk.CTkLabel(
            self.preview_frame,
            text="Кадр не выбран",
            font=ctk.CTkFont(size=11),
            text_color="gray70"
        )
        self.preview_time_label.grid(row=2, column=0, pady=(0, 1))

        self.preview_slider = ctk.CTkSlider(
            self.preview_frame,
            from_=0,
            to=1,
            command=self.on_preview_slider,
            height=16
        )
        self.preview_slider.grid(row=3, column=0, sticky="ew", padx=12, pady=(2, 3))
        self.preview_slider.set(0)

        self.preview_btn = ctk.CTkButton(
            self.preview_frame,
            text="Показать кадр",
            command=self.update_preview,
            height=30,
            font=ctk.CTkFont(size=12)
        )
        self.preview_btn.grid(row=4, column=0, pady=(0, 6))

        # === Параметры: всегда находятся в окне, без основной прокрутки ===
        self.conversion_frame = ctk.CTkFrame(self)
        self.conversion_frame.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 4))
        self.conversion_frame.grid_columnconfigure(0, weight=1)

        self.conversion_title = ctk.CTkLabel(
            self.conversion_frame,
            text="Параметры конвертации",
            font=ctk.CTkFont(size=14, weight="bold")
        )
        self.conversion_title.grid(row=0, column=0, pady=(5, 3))

        self.operation_var = tk.StringVar(master=self, value="Авто по формату")
        self.encoder_var = tk.StringVar(master=self, value="Авто")
        self.container_var = tk.StringVar(master=self, value="MPEG-TS (.ts)")
        self.audio_var = tk.StringVar(master=self, value="AC3 448 kbps")
        self.quality_mode_var = tk.StringVar(master=self, value="Авто (Magic)")
        self._last_quality_mode_label = self.quality_mode_var.get()

        self.fields_frame = ctk.CTkFrame(self.conversion_frame, fg_color="transparent")
        self.fields_frame.grid(row=1, column=0, sticky="ew", padx=7, pady=(0, 2))

        self.operation_frame, self.operation_menu = self._make_option(
            self.fields_frame, "Операция", self.operation_var,
            ["Авто по формату", "2D → TS", "Full SBS → Half SBS", "Half TAB → Half SBS"]
        )
        self.operation_menu.configure(command=self._on_operation_changed)

        self.encoder_choices = ["Авто"]
        if "nvenc" in self.hw_info.get("available", []):
            self.encoder_choices.append("NVIDIA NVENC")
        if "qsv" in self.hw_info.get("available", []):
            self.encoder_choices.append("Intel QSV")
        self.encoder_frame, self.encoder_menu = self._make_option(
            self.fields_frame, "Кодировщик", self.encoder_var, self.encoder_choices
        )
        self.encoder_menu.configure(command=self._on_encoder_changed)

        self.container_frame, self.container_menu = self._make_option(
            self.fields_frame, "Контейнер", self.container_var,
            ["MPEG-TS (.ts)", "MP4 (.mp4)"]
        )

        self.audio_frame, self.audio_menu = self._make_option(
            self.fields_frame, "Аудиодорожки", self.audio_var,
            ["AC3 448 kbps", "Копировать оригинал"]
        )

        self.quality_frame, self.quality_menu = self._make_option(
            self.fields_frame, "Режим качества", self.quality_mode_var,
            ["Авто (Magic)", "VBR Mbps", "VBR MB/s", "Размер файла (МБ)", "CQ / ICQ", "CBR Mbps"]
        )
        self.quality_menu.configure(command=self._on_quality_mode_changed)

        self.quality_value_frame = ctk.CTkFrame(self.fields_frame, fg_color="transparent")
        self.quality_value_label = ctk.CTkLabel(
            self.quality_value_frame, text="Значение  ⓘ", anchor="w"
        )
        self.quality_value_label.pack(fill="x")
        self.quality_value_label.bind("<Enter>", self._schedule_quality_tooltip)
        self.quality_value_label.bind("<Leave>", self._hide_quality_tooltip)
        self.quality_value_entry = ctk.CTkEntry(
            self.quality_value_frame,
            placeholder_text="Автоматический подбор — ввод не требуется",
            height=30
        )
        self.quality_value_entry.pack(fill="x")

        self.preset_frame = ctk.CTkFrame(self.fields_frame, fg_color="transparent")
        ctk.CTkLabel(self.preset_frame, text="Пресет кодирования", anchor="w").pack(fill="x")
        self.preset_var = tk.StringVar(master=self, value="slower")
        self.preset_menu = ctk.CTkOptionMenu(
            self.preset_frame,
            values=list(preset_options_for_encoder(self.hw_info.get("type"))),
            variable=self.preset_var,
            command=self._on_preset_changed,
            height=30,
            font=ctk.CTkFont(size=11),
            dropdown_font=ctk.CTkFont(size=11),
        )
        self.preset_menu.pack(fill="x")

        self.test_frame = ctk.CTkFrame(self.fields_frame, fg_color="transparent")
        self.test_var = tk.BooleanVar(master=self, value=False)
        self.test_checkbox = ctk.CTkCheckBox(
            self.test_frame, text="Тестовый фрагмент", variable=self.test_var,
            height=26, font=ctk.CTkFont(size=12)
        )
        self.test_checkbox.pack(anchor="w", pady=(0, 1))
        self.test_minutes_entry = ctk.CTkEntry(
            self.test_frame, placeholder_text="Минуты (по умолчанию 2)", height=30
        )
        self.test_minutes_entry.pack(fill="x")

        self._conversion_field_frames = [
            self.operation_frame, self.encoder_frame, self.container_frame, self.audio_frame,
            self.quality_frame, self.quality_value_frame, self.preset_frame, self.test_frame,
        ]

        self.conversion_row3 = ctk.CTkFrame(self.conversion_frame, fg_color="transparent")
        self.conversion_row3.grid(row=2, column=0, sticky="ew", padx=8, pady=(2, 5))
        self.conversion_row3.grid_columnconfigure(1, weight=1)

        self.convert_button = ctk.CTkButton(
            self.conversion_row3,
            text="Начать конвертацию",
            command=self.start_conversion,
            height=32,
            width=220,
            font=ctk.CTkFont(size=12)
        )
        self.convert_button.grid(row=0, column=0, padx=(0, 8), sticky="w")

        self.progress_bar = ctk.CTkProgressBar(self.conversion_row3, height=14)
        self.progress_bar.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        self.progress_bar.set(0)

        self.conversion_status = ctk.CTkLabel(
            self.conversion_row3, text="Ожидание", width=110, anchor="e",
            font=ctk.CTkFont(size=11)
        )
        # Прижимаем скорость к правому краю её области, а таймер — к левому
        # краю своей. Так текст не разъезжается по краям широких виджетов.
        self.conversion_status.grid(row=0, column=2, sticky="e", padx=(0, 5))

        # Реальное прошедшее время и расчётное время до завершения.
        # ETA вычисляется по средней скорости обработки с начала конвертации.
        self.conversion_timer = ctk.CTkLabel(
            self.conversion_row3,
            text="Прошло 00:00:00\nОсталось —",
            width=145,
            height=30,
            anchor="w",
            justify="left",
            font=ctk.CTkFont(size=10),
        )
        # Оставляем заметный зазор между показателем скорости и двумя таймерами.
        self.conversion_timer.grid(row=0, column=3, sticky="w", padx=(20, 0))

        # === Статус оборудования — всегда виден под параметрами ===
        hw_text = f"Обнаружено железо: {self.hw_info['name']}"
        self.hw_label = ctk.CTkLabel(
            self,
            text=hw_text,
            font=ctk.CTkFont(size=11),
            text_color="gray60"
        )
        self.hw_label.grid(row=3, column=0, sticky="ew", pady=(0, 4))

        self._update_preset_options(reset_manual=True)
        self._layout_conversion_fields()
        self._on_operation_changed(self.operation_var.get())
        self._refresh_quality_value_field()
        self.bind("<Configure>", self._on_root_resize, add="+")
        self.protocol("WM_DELETE_WINDOW", self.on_window_close)
        self.after(150, self._apply_responsive_layout)
        if self._restore_maximized:
            self.after(250, self._restore_maximized_window)

    def _make_option(self, parent, label, variable, values):
        """Создаёт адаптивный блок с подписью и выпадающим списком."""
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        ctk.CTkLabel(
            frame, text=label, anchor="w", font=ctk.CTkFont(size=11)
        ).pack(fill="x", pady=(0, 1))
        menu = ctk.CTkOptionMenu(
            frame, values=values, variable=variable, height=30,
            font=ctk.CTkFont(size=11), dropdown_font=ctk.CTkFont(size=11)
        )
        menu.pack(fill="x")
        return frame, menu

    def _on_operation_changed(self, selected=None):
        """Убирает настройки кодирования для операции простого remux в TS."""
        selected = selected or self.operation_var.get()
        is_remux = selected == "2D → TS"

        if is_remux:
            # Исходный BAT для 2D -> TS копирует видео и звук без перекодирования.
            # Поэтому параметры энкодера, контейнера и качества здесь не применяются.
            self.container_var.set("MPEG-TS (.ts)")
            self.audio_var.set("Копировать оригинал")

        state = "disabled" if is_remux else "normal"
        for widget in (
            self.encoder_menu,
            self.container_menu,
            self.audio_menu,
            self.quality_menu,
            self.preset_menu,
        ):
            widget.configure(state=state)
        self._refresh_quality_value_field()

    def _active_encoder_for_preset(self):
        """Resolve Auto/manual UI selection to nvenc or qsv for preset choices."""
        selected = self.encoder_var.get()
        if selected == "NVIDIA NVENC":
            return "nvenc"
        if selected == "Intel QSV":
            return "qsv"
        active = self.hw_info.get("type")
        return active if active in ("nvenc", "qsv") else None

    def _update_preset_options(self, reset_manual=False):
        """Refresh preset choices and preselect the BAT-compatible default."""
        if reset_manual:
            self._preset_manual = False
        encoder = self._active_encoder_for_preset()
        options = preset_options_for_encoder(encoder)
        self.preset_menu.configure(values=list(options))

        current = self.preset_var.get()
        if not self._preset_manual or current not in options:
            quality_mode = "auto" if self.quality_mode_var.get() == "Авто (Magic)" else "manual"
            default = recommended_preset(
                encoder,
                quality_mode=quality_mode,
                height=int(self.video_info.get("height", 0) or 0),
                bitrate_bps=int(self.video_info.get("bitrate_bps", 0) or 0),
            )
            self.preset_var.set(default)
            self.preset_menu.set(default)

    def _on_encoder_changed(self, selected=None):
        # A different encoder has a different preset vocabulary; reset to its default.
        self._preset_manual = False
        self._update_preset_options()
        self._on_operation_changed(self.operation_var.get())
        self._refresh_quality_value_field()

    def _on_quality_mode_changed(self, selected=None):
        # A value entered for another mode has a different unit/meaning; clear it.
        # Important: Auto mode disables CTkEntry. Re-enable it before delete(),
        # otherwise the old Auto placeholder/value can survive the mode switch.
        current_mode = selected or self.quality_mode_var.get()
        mode_changed = current_mode != self._last_quality_mode_label
        self._last_quality_mode_label = current_mode
        # Auto Magic can use a different NVENC default (p6) for demanding sources.
        self._update_preset_options()
        self._refresh_quality_value_field(clear=mode_changed)

    def _on_preset_changed(self, selected=None):
        self._preset_manual = True

    def _current_quality_field_help(self):
        """Return the current help text for the selected encoder, device and mode."""
        encoder = self._active_encoder_for_preset()
        names = self.hw_info.get("names", {})
        if encoder:
            device_name = names.get(encoder, "")
        else:
            device_name = self.hw_info.get("name", "Аппаратное устройство не определено")
        return build_quality_field_help(
            self.quality_mode_var.get(),
            encoder,
            device_name,
            height=int(self.video_info.get("height", 0) or 0),
            bitrate_bps=int(self.video_info.get("bitrate_bps", 0) or 0),
            duration_seconds=float(self.video_info.get("duration", 0) or 0),
            remux=self.operation_var.get() == "2D → TS",
        )

    def _refresh_quality_value_field(self, *, clear=False):
        """Update placeholder and availability whenever mode/encoder/source changes."""
        if not hasattr(self, "quality_value_entry"):
            return
        help_info = self._current_quality_field_help()
        apply_quality_field_state(self.quality_value_entry, help_info, clear=clear)
        self.quality_value_label.configure(
            text_color=("gray85" if help_info.enabled else "gray55")
        )
        # If a tooltip is open, rebuild it so it never displays stale settings.
        if self._quality_tooltip_window is not None:
            self._hide_quality_tooltip()

    def _schedule_quality_tooltip(self, _event=None):
        """Show a short delayed tooltip over the Value label."""
        if self._quality_tooltip_job is not None:
            try:
                self.after_cancel(self._quality_tooltip_job)
            except Exception:
                pass
        self._quality_tooltip_job = self.after(350, self._show_quality_tooltip)

    def _show_quality_tooltip(self):
        self._quality_tooltip_job = None
        if not self.winfo_exists() or not self.quality_value_label.winfo_exists():
            return
        self._hide_quality_tooltip()
        tooltip = tk.Toplevel(self)
        tooltip.wm_overrideredirect(True)
        try:
            tooltip.attributes("-topmost", True)
        except tk.TclError:
            pass
        label = tk.Label(
            tooltip,
            text=self._current_quality_field_help().tooltip,
            justify="left",
            anchor="w",
            # Larger, roomier tooltip: readable at normal Windows display scaling.
            wraplength=560,
            bg="#252525",
            fg="#f2f2f2",
            relief="solid",
            borderwidth=2,
            padx=16,
            pady=14,
            font=("Segoe UI", 12),
        )
        label.pack()
        tooltip.update_idletasks()
        x = self.quality_value_label.winfo_rootx()
        y = self.quality_value_label.winfo_rooty() + self.quality_value_label.winfo_height() + 5
        tip_width = tooltip.winfo_reqwidth()
        tip_height = tooltip.winfo_reqheight()
        screen_width = tooltip.winfo_screenwidth()
        screen_height = tooltip.winfo_screenheight()
        x = max(6, min(x, screen_width - tip_width - 6))
        if y + tip_height > screen_height - 6:
            y = max(6, self.quality_value_label.winfo_rooty() - tip_height - 5)
        tooltip.geometry(f"+{x}+{y}")
        self._quality_tooltip_window = tooltip

    def _hide_quality_tooltip(self, _event=None):
        if self._quality_tooltip_job is not None:
            try:
                self.after_cancel(self._quality_tooltip_job)
            except Exception:
                pass
            self._quality_tooltip_job = None
        if self._quality_tooltip_window is not None:
            try:
                self._quality_tooltip_window.destroy()
            except Exception:
                pass
            self._quality_tooltip_window = None

    def _layout_conversion_fields(self):
        """Раскладывает поля в 4 колонки на широком экране и в 2 на узком."""
        width = max(self.winfo_width(), self.winfo_screenwidth() if self.state() == "zoomed" else 0)
        columns = 4 if width >= 900 else 2
        if columns == self._last_layout_columns:
            return
        self._last_layout_columns = columns
        for child in self.fields_frame.grid_slaves():
            child.grid_forget()
        # Сбрасываем старые веса, иначе после перехода с 4 колонок на 2
        # оставшиеся пустые колонки продолжат занимать место.
        for col in range(4):
            self.fields_frame.grid_columnconfigure(
                col, weight=1 if col < columns else 0,
                uniform="conversion_fields" if col < columns else ""
            )
        for index, frame in enumerate(self._conversion_field_frames):
            row, col = divmod(index, columns)
            frame.grid(row=row, column=col, sticky="ew", padx=4, pady=3)

    def _get_window_state_path(self):
        """Путь к пользовательским настройкам окна (не внутри папки программы)."""
        app_data = os.environ.get("APPDATA")
        if app_data:
            settings_dir = os.path.join(app_data, "3DConverter")
        else:
            settings_dir = os.path.join(os.path.expanduser("~"), ".3d_converter")
        return os.path.join(settings_dir, "window_state.json")

    def _load_window_state(self):
        """Загружает размер/позицию и проверяет, что окно останется видимым."""
        default = {"geometry": "1360x900+60+40", "maximized": False}
        try:
            with open(self._window_state_path, "r", encoding="utf-8") as settings_file:
                data = json.load(settings_file)
            geometry = str(data.get("geometry", default["geometry"]))
            match = re.fullmatch(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)", geometry)
            if not match:
                return default

            width, height, x, y = map(int, match.groups())
            screen_width = self.winfo_screenwidth()
            screen_height = self.winfo_screenheight()
            width = max(900, min(width, screen_width))
            height = max(560, min(height, max(560, screen_height - 60)))

            # Если монитор отключили или разрешение изменилось, возвращаем окно
            # в видимую область основного экрана.
            if (x > screen_width - 120 or y > screen_height - 80
                    or x + width < 120 or y + height < 80):
                x, y = 40, 40

            safe_geometry = f"{width}x{height}{x:+d}{y:+d}"
            return {
                "geometry": safe_geometry,
                "maximized": bool(data.get("maximized", False)),
            }
        except (OSError, ValueError, TypeError, AttributeError, tk.TclError, json.JSONDecodeError):
            return default

    def _save_window_state(self):
        """Сохраняет последнюю нормальную геометрию и состояние развёртывания."""
        try:
            window_state = self.state()
            if window_state == "normal":
                self._last_normal_geometry = self.geometry()
            geometry = self._last_normal_geometry
            match = re.fullmatch(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)", geometry or "")
            if not match:
                return

            os.makedirs(os.path.dirname(self._window_state_path), exist_ok=True)
            payload = {
                "geometry": geometry,
                "maximized": window_state == "zoomed",
            }
            temporary_path = self._window_state_path + ".tmp"
            with open(temporary_path, "w", encoding="utf-8") as settings_file:
                json.dump(payload, settings_file, ensure_ascii=False, indent=2)
            os.replace(temporary_path, self._window_state_path)
        except (OSError, tk.TclError, AttributeError):
            # Ошибка сохранения настроек окна не должна мешать закрытию программы.
            pass

    def _restore_maximized_window(self):
        """Возвращает развёрнутое состояние, если пользователь закрыл окно так."""
        try:
            if self.winfo_exists():
                self.state("zoomed")
        except tk.TclError:
            pass

    def _on_root_resize(self, event=None):
        """Перекомпоновывает интерфейс и запоминает нормальную геометрию окна."""
        if event is not None and event.widget is not self:
            return
        try:
            if self.state() == "normal" and self.winfo_width() >= 500 and self.winfo_height() >= 350:
                self._last_normal_geometry = self.geometry()
        except tk.TclError:
            pass
        if self._layout_resize_job is not None:
            try:
                self.after_cancel(self._layout_resize_job)
            except Exception:
                pass
        self._layout_resize_job = self.after(90, self._apply_responsive_layout)

    def _apply_responsive_layout(self):
        """Подстраивает компоновку и плотность под текущее клиентское окно."""
        self._layout_resize_job = None
        if not self.winfo_exists():
            return

        self._layout_conversion_fields()
        height = self.winfo_height()
        compact = height < 720
        very_compact = height < 620

        # Сжимаем вертикальные интервалы и шрифты, не скрывая элементы.
        title_size = 20 if compact else 23
        body_size = 10 if very_compact else (11 if compact else 12)
        panel_title_size = 12 if compact else 14

        self.header.configure(font=ctk.CTkFont(size=title_size, weight="bold"))
        # Высота бейджа следует за размером шрифта: строка текста + 2 px внешнего поля.
        self.version_label.configure(
            font=ctk.CTkFont(size=8 if compact else 9, weight="bold"),
            height=13 if compact else 14,
        )
        self.subtitle.configure(font=ctk.CTkFont(size=body_size))
        self.select_btn.configure(height=30 if compact else 34)
        self.info_title.configure(font=ctk.CTkFont(size=panel_title_size, weight="bold"))
        self.rec_title.configure(font=ctk.CTkFont(size=panel_title_size, weight="bold"))
        self.preview_title.configure(font=ctk.CTkFont(size=panel_title_size, weight="bold"))
        self.conversion_title.configure(font=ctk.CTkFont(size=panel_title_size, weight="bold"))

        text_font = ctk.CTkFont(size=body_size)
        self.info_text.configure(font=text_font)
        self.rec_text.configure(font=text_font)

        button_height = 27 if compact else 30
        self.preview_btn.configure(height=button_height)
        self.convert_button.configure(height=29 if compact else 32)
        self.preview_slider.configure(height=14 if compact else 16)

        # В малых окнах сокращаем отступы шапки и панелей.
        header_top = 1 if compact else 5
        self.header_frame.grid_configure(pady=(header_top, 1))
        self.content_frame.grid_configure(pady=(2, 3), padx=8 if compact else 12)
        self.conversion_frame.grid_configure(padx=8 if compact else 12, pady=(0, 2))
        self.info_title.grid_configure(pady=(3 if compact else 5, 1))
        self.rec_title.grid_configure(pady=(3 if compact else 5, 1))
        self.preview_title.grid_configure(pady=(3 if compact else 5, 2))
        self.preview_image_label.grid_configure(padx=5 if compact else 8, pady=(0, 2 if compact else 4))
        self.preview_time_label.grid_configure(pady=(0, 0))
        self.preview_slider.grid_configure(padx=8 if compact else 12, pady=(1, 2))
        self.preview_btn.grid_configure(pady=(0, 3 if compact else 6))
        for field_frame in self._conversion_field_frames:
            field_frame.grid_configure(padx=3 if compact else 4,
                                       pady=1 if compact else 3)

        # Оставляем за содержимым центральную часть окна, а не заставляем
        # всю страницу расти по минимальной высоте внутренних виджетов.
        self.update_idletasks()

    def on_window_close(self):
        """Не закрывает окно раньше завершения остановки FFmpeg."""
        active_states = {"starting", "running", "paused", "stopping", "finishing"}
        if self._conversion_state in active_states:
            if not messagebox.askyesno(
                "Закрыть программу",
                "Сейчас выполняется конвертация. Остановить её и выйти?",
                parent=self,
            ):
                return
            self._close_when_finished = True
            self._request_conversion_stop(show_message=False)
            return

        self._save_window_state()
        self.destroy()

    def on_preview_slider(self, value):
        """Показывает выбранное время; кадр извлекается по нажатию кнопки."""
        seconds = max(0, int(float(value)))
        minutes, secs = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        self.preview_time_label.configure(
            text=f"Кадр на отметке {hours:02d}:{minutes:02d}:{secs:02d} — нажмите «Показать кадр»"
        )

    def _on_preview_area_resize(self, _event=None):
        """Планирует перерисовку кадра после изменения размеров панели."""
        if self.preview_source_image is None:
            return
        if self._preview_resize_job is not None:
            try:
                self.after_cancel(self._preview_resize_job)
            except Exception:
                pass
        # Небольшая задержка не позволяет пересчитывать картинку десятки раз
        # во время перетаскивания границы окна.
        self._preview_resize_job = self.after(70, self._render_preview_image)

    def _render_preview_image(self):
        """Вписывает исходный кадр в доступную область без искажения пропорций."""
        self._preview_resize_job = None
        source = self.preview_source_image
        if source is None or not self.preview_image_label.winfo_exists():
            return

        available_width = self.preview_image_label.winfo_width() - 24
        available_height = self.preview_image_label.winfo_height() - 24
        if available_width < 40 or available_height < 40:
            return

        source_width, source_height = source.size
        if source_width <= 0 or source_height <= 0:
            return

        # Масштабируем и вверх, и вниз, но всегда сохраняем исходное соотношение сторон.
        scale = min(available_width / source_width, available_height / source_height)
        target_size = (
            max(1, int(round(source_width * scale))),
            max(1, int(round(source_height * scale))),
        )
        if target_size == self._preview_last_render_size:
            return

        display_image = source.resize(target_size, Image.Resampling.LANCZOS)
        self.preview_ctk_image = ctk.CTkImage(
            light_image=display_image,
            dark_image=display_image,
            size=target_size,
        )
        self.preview_image_label.configure(image=self.preview_ctk_image, text="")
        self._preview_last_render_size = target_size

    def update_preview(self):
        """Извлекает один кадр выбранного видео через FFmpeg и показывает его в окне."""
        if not self.video_path:
            return

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            self.preview_image_label.configure(
                image=None,
                text="FFmpeg не найден.\nДобавьте FFmpeg в PATH, чтобы включить предпросмотр."
            )
            self.preview_ctk_image = None
            self.preview_source_image = None
            self._preview_last_render_size = None
            return

        timestamp = max(0.0, float(self.preview_slider.get()))
        command = [
            ffmpeg,
            "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{timestamp:.3f}",
            "-i", self.video_path,
            "-frames:v", "1",
            "-vf", "scale=1920:1200:force_original_aspect_ratio=decrease",
            "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"
        ]

        self.preview_btn.configure(state="disabled", text="Извлечение кадра...")
        self.update_idletasks()
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
            if result.returncode != 0 or not result.stdout:
                error_text = result.stderr.decode("utf-8", errors="replace").strip()
                raise RuntimeError(error_text or "Не удалось извлечь кадр из видео.")

            with Image.open(BytesIO(result.stdout)) as source_image:
                # Храним полноразмерный кадр: при изменении окна не растягиваем
                # уже уменьшенную копию, а каждый раз строим изображение заново.
                self.preview_source_image = source_image.convert("RGB")
            self._preview_last_render_size = None
            self.update_idletasks()
            self._render_preview_image()
            self.on_preview_slider(timestamp)
            # Убираем подсказку о необходимости повторного нажатия после загрузки кадра.
            seconds = max(0, int(timestamp))
            minutes, secs = divmod(seconds, 60)
            hours, minutes = divmod(minutes, 60)
            self.preview_time_label.configure(
                text=f"Предпросмотр: {hours:02d}:{minutes:02d}:{secs:02d}"
            )
        except Exception as error:
            self.preview_ctk_image = None
            self.preview_source_image = None
            self._preview_last_render_size = None
            self.preview_image_label.configure(
                image=None,
                text=f"Не удалось показать кадр.\n{str(error)[:220]}"
            )
        finally:
            self.preview_btn.configure(state="normal", text="Показать кадр")

    def _test_hardware_encoder(self, encoder):
        """Проверяет не наличие имени кодировщика, а реальный запуск аппаратного кодирования."""
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return False

        command = [
            ffmpeg,
            "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30",
            "-vf", "format=nv12",
            "-frames:v", "30",
            "-an",
            "-c:v", encoder,
            "-f", "null", "-"
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, OSError):
            return False

    def _detect_qsv_device_name(self):
        """Read the actual Intel adapter name from a one-frame verbose QSV probe."""
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return None
        command = [
            ffmpeg, "-hide_banner", "-loglevel", "verbose",
            "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=1",
            "-vf", "format=nv12", "-frames:v", "1", "-an",
            "-c:v", "h264_qsv", "-f", "null", "-",
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (subprocess.SubprocessError, OSError):
            return None
        log_text = (result.stdout or "") + "\n" + (result.stderr or "")
        match = re.search(r"Using device\s+[0-9a-fA-F:]+\s+\(([^)]+)\)", log_text)
        return match.group(1).strip() if match else None

    def detect_hardware(self):
        """Проверяет фактический запуск NVENC и QSV, а не только наличие энкодера."""
        available = []
        names = {}

        if shutil.which("nvidia-smi"):
            try:
                result = subprocess.run(
                    ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                    capture_output=True, text=True, timeout=3,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                )
                if result.returncode == 0 and result.stdout.strip():
                    gpu_name = result.stdout.strip().splitlines()[0]
                    if self._test_hardware_encoder("h264_nvenc"):
                        available.append("nvenc")
                        names["nvenc"] = f"NVIDIA ({gpu_name}) — NVENC проверен"
            except (subprocess.SubprocessError, OSError):
                pass

        if self._test_hardware_encoder("h264_qsv"):
            available.append("qsv")
            qsv_device = self._detect_qsv_device_name()
            names["qsv"] = (
                f"{qsv_device} (Intel Quick Sync / QSV)"
                if qsv_device else "Intel Quick Sync Video (QSV)"
            )

        # При автоматическом выборе сохраняем прежний приоритет: NVIDIA, затем Intel.
        if "nvenc" in available:
            primary = "nvenc"
        elif "qsv" in available:
            primary = "qsv"
        else:
            primary = "cpu"

        if primary == "cpu":
            return {
                "type": "cpu",
                "name": "Аппаратные кодировщики NVENC/QSV не прошли проверку",
                "encoder": "недоступен",
                "available": available,
            }
        return {
            "type": primary,
            "name": names[primary],
            "encoder": "h264_nvenc" if primary == "nvenc" else "h264_qsv",
            "available": available,
            "names": names,
        }

    def select_video(self):
        path = filedialog.askopenfilename(
            title="Выберите видеофайл",
            filetypes=[
                ("Видео файлы", "*.mp4 *.mkv *.ts *.avi *.mov *.m2ts *.mts"),
                ("Все файлы", "*.*")
            ]
        )
        if path:
            self.video_path = path
            self.file_label.configure(text=os.path.basename(path))
            self.analyze_video()

    def analyze_video(self):
        if not self.video_path:
            return

        # Проверяем наличие ffprobe
        if not shutil.which("ffprobe"):
            messagebox.showerror(
                "Ошибка", 
                "ffprobe не найден!\n\nУбедитесь, что FFmpeg установлен и добавлен в PATH."
            )
            return

        try:
            # Получаем информацию о видео
            cmd = [
                "ffprobe", "-v", "quiet",
                "-print_format", "json",
                "-show_format", "-show_streams",
                self.video_path
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            
            if result.returncode != 0:
                raise Exception("Не удалось проанализировать файл")

            data = json.loads(result.stdout)
            
            # Ищем видеопоток
            video_stream = None
            for stream in data.get("streams", []):
                if stream.get("codec_type") == "video":
                    video_stream = stream
                    break

            if not video_stream:
                raise Exception("Видеопоток не найден")

            width = int(video_stream.get("width", 0))
            height = int(video_stream.get("height", 0))
            duration = float(data.get("format", {}).get("duration", 0))
            bitrate_bps = int(data.get("format", {}).get("bit_rate", 0) or 0)
            bitrate = bitrate_bps // 1000  # кбит/с

            # Определяем тип 3D
            video_type = self.detect_3d_type(
                width, height, video_stream, self.video_path, data.get("format", {}).get("tags", {})
            )

            self.video_info = {
                "width": width,
                "height": height,
                "duration": duration,
                "bitrate": bitrate,
                "bitrate_bps": bitrate_bps,
                "type": video_type,
                "codec": video_stream.get("codec_name", "unknown")
            }

            # Update the recommended default when source bitrate/resolution is known.
            # Preserve a preset that the user explicitly selected.
            self._update_preset_options()
            self._refresh_quality_value_field()
            self.show_info()
            self.show_recommendations()

            # По умолчанию показываем кадр примерно на 10% длительности,
            # но не дальше 10-й секунды — начало фильма иногда бывает чёрным.
            preview_start = min(max(duration * 0.10, 1.0), 10.0)
            preview_start = min(preview_start, max(duration, 0.0))
            self.preview_slider.configure(to=max(duration, 1.0))
            self.preview_slider.set(preview_start)
            self.update_preview()

        except Exception as e:
            messagebox.showerror("Ошибка анализа", str(e))

    def detect_3d_type(self, width, height, stream, filename="", format_tags=None):
        """Определяет 3D-формат по явным меткам имени файла и метаданным."""
        # 1. Явная метка в имени файла обычно содержит точное обозначение
        # Half/Full и SBS/TAB. Например: 3D-halfOU.mkv.
        base_name = os.path.basename(filename or "").lower()
        compact_name = re.sub(r"[^a-z0-9]+", "", base_name)

        if re.search(r"half(?:ou|tab|tb)", compact_name) or re.search(r"(?:ou|tab|tb)half", compact_name):
            return "Half TAB"
        if re.search(r"full(?:ou|tab|tb)", compact_name) or re.search(r"(?:ou|tab|tb)full", compact_name):
            return "Full TAB"
        if "halfsbs" in compact_name or "sbshalf" in compact_name:
            return "Half SBS"
        if "fullsbs" in compact_name or "sbsfull" in compact_name:
            return "Full SBS"

        # 2. Стереометаданные FFprobe: сначала теги видеопотока,
        # затем теги контейнера. Они могут указывать расположение глаз.
        tags = stream.get("tags", {}) or {}
        format_tags = format_tags or {}
        stereo_mode = " ".join([
            str(tags.get("stereo_mode", "")),
            str(format_tags.get("stereo_mode", "")),
        ]).lower().replace("-", "_").replace(" ", "_")

        if any(marker in stereo_mode for marker in ("left_right", "right_left", "side_by_side")):
            return "Full SBS" if width / max(height, 1) > 2.5 else "Half SBS"

        if any(marker in stereo_mode for marker in ("top_bottom", "bottom_top", "over_under")):
            return "TAB (Top/Bottom; Half/Full не уточнён)"

        # 3. По одному разрешению 1920x1080 нельзя надёжно различить
        # обычное 2D, Half SBS и Half TAB — не выдаём догадку за факт.
        aspect = width / height if height > 0 else 0
        if aspect > 2.8:
            return "Full SBS (предположительно)"
        if 1.6 <= aspect <= 2.0 and width >= 1280:
            return "Не определено: возможно 2D, Half SBS или Half TAB"
        if aspect < 1.0:
            return "Возможно TAB (Top/Bottom), Half/Full не определён"
        return "Обычное 2D (предположительно)"

    def _resolve_operation(self):
        selected = self.operation_var.get()
        detected_type = self.video_info.get("type", "")
        if selected == "Авто по формату":
            if detected_type == "Half TAB":
                return "half_tab_to_half_sbs"
            if detected_type == "Full SBS":
                return "full_sbs_to_half_sbs"
            if detected_type.startswith("Обычное 2D"):
                return "remux_2d_to_ts"
            raise ProfileError(
                f"Для формата «{detected_type}» автоматическая операция не определена. "
                "Выберите операцию вручную. Простая перепаковка в TS не меняет геометрию видео."
            )

        selected_map = {
            "2D → TS": "remux_2d_to_ts",
            "Full SBS → Half SBS": "full_sbs_to_half_sbs",
            "Half TAB → Half SBS": "half_tab_to_half_sbs",
        }
        operation = selected_map.get(selected)
        if not operation:
            raise ProfileError("Неизвестная операция конвертации.")

        # Remux не меняет геометрию, поэтому ручной выбор 2D -> TS безопасен
        # даже тогда, когда анализатор не смог определить исходный стереоформат.
        if operation == "remux_2d_to_ts":
            return operation

        if operation == "full_sbs_to_half_sbs":
            # Ручной выбор конкретной операции сам является подтверждением
            # пользователем исходной геометрии. Для широкоэкранного источника,
            # который анализатор пометил как Full SBS (предположительно),
            # не показываем повторный диалог подтверждения.
            if detected_type == "Full SBS" or detected_type.startswith("Full SBS ("):
                return operation

        if operation == "half_tab_to_half_sbs" and detected_type == "Half TAB":
            return operation

        raise ProfileError(
            f"Выбрана операция «{selected}», но анализатор определил «{detected_type}». "
            "Проверьте исходный формат и выберите соответствующую операцию."
        )

    def _resolve_encoder(self):
        selected = self.encoder_var.get()
        if selected == "Авто":
            encoder = self.hw_info.get("type")
            if encoder not in ("nvenc", "qsv"):
                raise ProfileError("Не обнаружен работающий аппаратный кодировщик NVENC или QSV.")
            return encoder
        mapping = {"NVIDIA NVENC": "nvenc", "Intel QSV": "qsv"}
        encoder = mapping.get(selected)
        if encoder not in self.hw_info.get("available", []):
            raise ProfileError(f"{selected} не прошёл проверку доступности на этом компьютере.")
        return encoder

    def _quality_settings(self):
        mode_map = {
            "Авто (Magic)": "auto",
            "VBR Mbps": "vbr_mbps",
            "VBR MB/s": "vbr_mbytes",
            "Размер файла (МБ)": "target_size",
            "CQ / ICQ": "quality",
            "CBR Mbps": "cbr_mbps",
        }
        mode_label = self.quality_mode_var.get()
        mode = mode_map.get(mode_label)
        if mode is None:
            raise ProfileError("Неизвестный режим качества.")
        raw_value = self.quality_value_entry.get().strip().replace(",", ".")
        value = None
        if mode != "auto":
            try:
                value = float(raw_value)
            except ValueError as error:
                examples = {
                    "vbr_mbps": "например 12 (Mbps)",
                    "vbr_mbytes": "например 2 (MB/s)",
                    "target_size": "например 4500 (МБ)",
                    "quality": "QSV: 1–51; NVENC: 15–28",
                    "cbr_mbps": "например 10 (Mbps)",
                }
                hint = examples.get(mode, "числовое значение")
                raise ProfileError(f"Для режима «{mode_label}» введите значение: {hint}.") from error
        preset = self.preset_var.get().strip() or None
        return QualitySettings(mode=mode, value=value, preset=preset)

    def _choose_existing_output_action(self, output_path):
        """Предлагает перезаписать результат, задать своё имя или отменить запуск."""
        result = {"path": None}
        required_extension = os.path.splitext(output_path)[1] or ".ts"
        suggested_path = next_available_variant(output_path)
        folder = os.path.dirname(output_path) or os.getcwd()

        dialog = ctk.CTkToplevel(self)
        dialog.title("Файл уже существует")
        dialog.geometry("620x230")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()

        message = (
            "Файл с таким именем уже существует:\n"
            f"{output_path}\n\n"
            "Выберите действие:"
        )
        ctk.CTkLabel(
            dialog,
            text=message,
            justify="left",
            anchor="w",
            wraplength=570,
        ).pack(fill="x", padx=22, pady=(22, 16))

        button_row = ctk.CTkFrame(dialog, fg_color="transparent")
        button_row.pack(fill="x", padx=18, pady=(0, 18))
        for column in range(3):
            button_row.grid_columnconfigure(column, weight=1)

        def finish(path):
            result["path"] = path
            dialog.destroy()

        def choose_custom_name():
            filetypes = (
                [("MPEG-TS (*.ts)", "*.ts"), ("Все файлы", "*.*")]
                if required_extension.lower() == ".ts"
                else [("MP4 (*.mp4)", "*.mp4"), ("Все файлы", "*.*")]
            )
            selected = filedialog.asksaveasfilename(
                parent=dialog,
                title="Сохранить результат под своим именем",
                initialdir=folder,
                initialfile=os.path.basename(suggested_path),
                defaultextension=required_extension,
                filetypes=filetypes,
                confirmoverwrite=False,
            )
            if not selected:
                return

            selected = ensure_required_extension(selected, required_extension)
            input_path = os.path.normcase(os.path.abspath(self.video_path or ""))
            selected_normalized = os.path.normcase(os.path.abspath(selected))
            if input_path and selected_normalized == input_path:
                messagebox.showerror(
                    "Недопустимое имя",
                    "Нельзя сохранить результат поверх исходного видео. Выберите другое имя.",
                    parent=dialog,
                )
                return
            # Confirm overwriting a custom target, including a file that may
            # have appeared after the Save As dialog was opened.
            if os.path.exists(selected) and not messagebox.askyesno(
                "Файл уже существует",
                f"Файл уже существует:\n{selected}\n\nПерезаписать его?",
                parent=dialog,
            ):
                return
            finish(selected)

        ctk.CTkButton(
            button_row,
            text="Перезаписать",
            command=lambda: finish(output_path),
            height=38,
        ).grid(row=0, column=0, padx=5, sticky="ew")
        ctk.CTkButton(
            button_row,
            text="Своё имя файла…",
            command=choose_custom_name,
            height=38,
        ).grid(row=0, column=1, padx=5, sticky="ew")
        ctk.CTkButton(
            button_row,
            text="Отмена",
            command=lambda: finish(None),
            height=38,
            fg_color="#555555",
            hover_color="#444444",
        ).grid(row=0, column=2, padx=5, sticky="ew")

        dialog.protocol("WM_DELETE_WINDOW", lambda: finish(None))
        dialog.bind("<Escape>", lambda _event: finish(None))
        self.wait_window(dialog)
        return result["path"]

    def start_conversion(self):
        if not self.video_path or not self.video_info:
            messagebox.showwarning("Нет видео", "Сначала выберите и проанализируйте видеофайл.")
            return
        try:
            operation = self._resolve_operation()
            # Пользователь мог отказаться от подтверждения предположительного Full SBS.
            if operation is None:
                return
            encoder = None if operation == "remux_2d_to_ts" else self._resolve_encoder()
            container = "ts" if operation == "remux_2d_to_ts" else (
                "mp4" if self.container_var.get().startswith("MP4") else "ts"
            )
            is_remux = operation == "remux_2d_to_ts"
            audio_mode = "copy" if is_remux or self.audio_var.get() == "Копировать оригинал" else "ac3"
            test_duration = None
            if self.test_var.get():
                raw_minutes = self.test_minutes_entry.get().strip().replace(",", ".")
                minutes = float(raw_minutes) if raw_minutes else 2.0
                if minutes <= 0:
                    raise ProfileError("Длительность тестового фрагмента должна быть больше нуля.")
                test_duration = int(round(minutes * 60))

            settings = ConversionSettings(
                input_path=self.video_path,
                operation=operation,
                encoder=encoder,
                container=container,
                audio_mode=audio_mode,
                quality=QualitySettings() if is_remux else self._quality_settings(),
                test_duration_seconds=test_duration,
            )
            source = SourceInfo(
                height=int(self.video_info.get("height", 0)),
                bitrate_bps=int(self.video_info.get("bitrate_bps", 0)),
                duration_seconds=float(self.video_info.get("duration", 0)),
            )
            plan = build_conversion_plan(settings, source)
            if os.path.exists(plan.output_path):
                selected_output = self._choose_existing_output_action(plan.output_path)
                if selected_output is None:
                    return
                if os.path.normcase(os.path.abspath(selected_output)) != os.path.normcase(os.path.abspath(plan.output_path)):
                    command = list(plan.command)
                    if not command or os.path.normcase(os.path.abspath(command[-1])) != os.path.normcase(os.path.abspath(plan.output_path)):
                        raise ProfileError("Не удалось безопасно изменить путь выходного файла в команде FFmpeg.")
                    command[-1] = selected_output
                    plan = replace(plan, command=command, output_path=selected_output)
        except (ProfileError, ValueError, OSError) as error:
            messagebox.showerror("Не удалось подготовить конвертацию", str(error))
            return

        self._conversion_state = "starting"
        self._conversion_started_at = time.perf_counter()
        self._pause_started_at = None
        self._total_paused_seconds = 0.0
        self._stop_requested = False
        self._close_when_finished = False
        self.convert_button.configure(
            state="disabled", text="Запуск FFmpeg...", command=self.show_conversion_action_dialog
        )
        self.select_btn.configure(state="disabled")
        self.progress_bar.set(0)
        self.conversion_status.configure(text="Запуск FFmpeg...")
        self.conversion_timer.configure(text="Прошло 00:00:00\nОсталось —")
        self._conversion_thread = threading.Thread(
            target=self._conversion_worker, args=(plan,), daemon=True
        )
        self._conversion_thread.start()

    def _conversion_worker(self, plan):
        # Измеряем реальное активное время работы, исключая периоды паузы.
        # perf_counter устойчив к изменениям системных часов Windows.
        conversion_started_at = self._conversion_started_at or time.perf_counter()
        duration = float(self.video_info.get("duration", 0))
        if "-t" in plan.command:
            try:
                duration = float(plan.command[plan.command.index("-t") + 1])
            except (ValueError, IndexError):
                pass
        log_tail = []
        return_code = -1
        try:
            process = subprocess.Popen(
                plan.command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self._conversion_process = process
            self.after(0, self._conversion_process_started)
            if self._stop_requested and process.poll() is None:
                process.terminate()
            if process.stdout is not None:
                for raw_line in process.stdout:
                    line = raw_line.strip()
                    if line:
                        log_tail.append(line)
                        log_tail = log_tail[-30:]
                    if "=" in line:
                        key, value = line.split("=", 1)
                        if key in ("out_time_us", "out_time_ms"):
                            try:
                                elapsed = int(value) / 1_000_000
                                fraction = min(1.0, max(0.0, elapsed / duration)) if duration > 0 else 0.0
                                wall_elapsed = self._active_conversion_elapsed(conversion_started_at)
                                self.after(
                                    0,
                                    self._set_conversion_progress,
                                    fraction,
                                    elapsed,
                                    duration,
                                    wall_elapsed,
                                )
                            except ValueError:
                                pass
                        elif key == "speed":
                            self.after(0, self._set_conversion_speed, value)
                        elif key == "progress" and value == "end":
                            self.after(0, self.progress_bar.set, 1)
            return_code = process.wait()
            self._conversion_state = "finishing"
            self._conversion_process = None
        except Exception as error:
            log_tail.append(str(error))
            self._conversion_state = "finishing"
            self._conversion_process = None
        self.after(0, self._conversion_finishing_ui)
        conversion_seconds = self._active_conversion_elapsed(conversion_started_at)
        success = return_code == 0
        details = "\n".join(log_tail[-12:])
        self.after(
            0,
            self._conversion_finished,
            success,
            plan,
            details,
            return_code,
            conversion_seconds,
        )

    def _conversion_process_started(self):
        """Активирует кнопку управления после фактического запуска FFmpeg."""
        if self._conversion_state == "starting":
            self._conversion_state = "running"
            self.convert_button.configure(
                state="normal",
                text="Остановить конвертацию",
                command=self.show_conversion_action_dialog,
            )
            self.conversion_status.configure(text="Подготовка...")

    def _conversion_finishing_ui(self):
        """Не позволяет отправить команду остановки уже завершившемуся FFmpeg."""
        if self._conversion_state == "finishing":
            self.convert_button.configure(state="disabled", text="Завершение...")

    def _set_conversion_speed(self, value):
        if self._conversion_state == "running":
            self.conversion_status.configure(text=f"Скорость {value}")

    def show_conversion_action_dialog(self):
        """Предлагает поставить конвертацию на паузу или остановить её."""
        if self._conversion_state not in {"running", "paused"}:
            return
        is_paused = self._conversion_state == "paused"

        dialog = ctk.CTkToplevel(self)
        dialog.title("Управление конвертацией")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()

        ctk.CTkLabel(
            dialog,
            text=("Конвертация приостановлена. Что сделать?" if is_paused
                  else "Что сделать с текущей конвертацией?"),
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(padx=20, pady=(18, 8))
        ctk.CTkLabel(
            dialog,
            text="При остановке частично обработанный выходной файл останется на диске.",
            font=ctk.CTkFont(size=11),
            text_color="gray70",
        ).pack(padx=20, pady=(0, 14))

        buttons = ctk.CTkFrame(dialog, fg_color="transparent")
        buttons.pack(fill="x", padx=12, pady=(0, 14))
        def close_dialog(action=None):
            try:
                dialog.grab_release()
            except Exception:
                pass
            if dialog.winfo_exists():
                dialog.destroy()
            if action == "pause":
                self.pause_conversion()
            elif action == "resume":
                self.resume_conversion()
            elif action == "stop":
                self._request_conversion_stop()

        for column in range(3):
            buttons.grid_columnconfigure(column, weight=1)

        first_action = "resume" if is_paused else "pause"
        first_label = "Возобновить" if is_paused else "Поставить на паузу"
        ctk.CTkButton(
            buttons, text=first_label, command=lambda: close_dialog(first_action),
            height=34, font=ctk.CTkFont(size=11),
        ).grid(row=0, column=0, padx=4, sticky="ew")
        ctk.CTkButton(
            buttons, text="Остановить конвертацию", command=lambda: close_dialog("stop"),
            height=34, font=ctk.CTkFont(size=11),
            fg_color="#a83232", hover_color="#842626",
        ).grid(row=0, column=1, padx=4, sticky="ew")
        ctk.CTkButton(
            buttons, text="Отмена", command=lambda: close_dialog(),
            height=34, font=ctk.CTkFont(size=11),
            fg_color="#555555", hover_color="#444444",
        ).grid(row=0, column=2, padx=4, sticky="ew")
        dialog.protocol("WM_DELETE_WINDOW", lambda: close_dialog())
        dialog.bind("<Escape>", lambda _event: close_dialog())
        self.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - dialog.winfo_reqwidth()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - dialog.winfo_reqheight()) // 2
        dialog.geometry(f"+{max(0, x)}+{max(0, y)}")
        dialog.focus_force()

    def pause_conversion(self):
        """Приостанавливает FFmpeg, не сбрасывая прогресс и таймеры."""
        process = self._conversion_process
        if self._conversion_state != "running" or process is None or psutil is None:
            if psutil is None:
                messagebox.showerror(
                    "Пауза недоступна",
                    "Для паузы требуется пакет psutil. Пересоберите приложение через build.py.",
                    parent=self,
                )
            return
        try:
            psutil.Process(process.pid).suspend()
        except psutil.NoSuchProcess:
            return
        except (psutil.AccessDenied, psutil.Error, OSError) as error:
            messagebox.showerror("Не удалось поставить на паузу", str(error), parent=self)
            return

        self._pause_started_at = time.perf_counter()
        self._conversion_state = "paused"
        self.conversion_status.configure(text="На паузе")
        self.convert_button.configure(
            state="normal", text="Управление паузой", command=self.show_conversion_action_dialog
        )

    def resume_conversion(self):
        """Возобновляет приостановленный процесс FFmpeg."""
        process = self._conversion_process
        if self._conversion_state != "paused" or process is None or psutil is None:
            return
        try:
            psutil.Process(process.pid).resume()
        except psutil.NoSuchProcess:
            return
        except (psutil.AccessDenied, psutil.Error, OSError) as error:
            messagebox.showerror("Не удалось возобновить конвертацию", str(error), parent=self)
            return

        now = time.perf_counter()
        if self._pause_started_at is not None:
            self._total_paused_seconds += max(0.0, now - self._pause_started_at)
        self._pause_started_at = None
        self._conversion_state = "running"
        self.conversion_status.configure(text="Возобновление...")
        self.convert_button.configure(
            state="normal",
            text="Остановить конвертацию",
            command=self.show_conversion_action_dialog,
        )

    def _request_conversion_stop(self, show_message=True):
        """Запрашивает остановку FFmpeg, сохраняя частично созданный выходной файл."""
        if self._conversion_state not in {"starting", "running", "paused", "stopping"}:
            return
        self._stop_requested = True
        self._conversion_state = "stopping"
        now = time.perf_counter()
        if self._pause_started_at is not None:
            self._total_paused_seconds += max(0.0, now - self._pause_started_at)
            self._pause_started_at = None

        self.convert_button.configure(state="disabled", text="Останавливаю...")
        self.conversion_status.configure(text="Остановка...")
        process = self._conversion_process
        if process is not None and process.poll() is None:
            try:
                if psutil is not None:
                    try:
                        psutil.Process(process.pid).resume()
                    except (psutil.NoSuchProcess, psutil.Error, OSError):
                        pass
                process.terminate()
            except OSError as error:
                self._stop_requested = False
                self._conversion_state = "running"
                self.convert_button.configure(
                    state="normal",
                    text="Остановить конвертацию",
                    command=self.show_conversion_action_dialog,
                )
                if show_message:
                    messagebox.showerror("Не удалось остановить FFmpeg", str(error), parent=self)

    def _active_conversion_elapsed(self, started_at=None):
        """Возвращает активное время конвертации, исключая паузы."""
        started = started_at or self._conversion_started_at or time.perf_counter()
        now = time.perf_counter()
        paused = self._total_paused_seconds
        if self._pause_started_at is not None:
            paused += max(0.0, now - self._pause_started_at)
        return max(0.0, now - started - paused)

    def _set_conversion_progress(self, fraction, media_elapsed, duration, wall_elapsed):
        """Обновляет шкалу, прошедшее время и оценку оставшегося времени."""
        if self._conversion_state != "running":
            return
        self.progress_bar.set(fraction)
        percent = int(fraction * 100)
        self.conversion_status.configure(text=f"{percent}%")

        elapsed_text = self._format_wall_time(wall_elapsed)
        remaining_text = "—"

        # Считаем среднюю скорость: сколько секунд исходного видео обработано
        # за одну реальную секунду. В первые секунды оценку не показываем,
        # чтобы не пугать пользователя скачущими значениями ETA.
        if fraction >= 1.0:
            remaining_text = "00:00:00"
        elif duration > 0 and media_elapsed >= 2.0 and wall_elapsed >= 2.0:
            average_speed = media_elapsed / wall_elapsed
            if average_speed > 0:
                remaining_seconds = max(0.0, duration - media_elapsed) / average_speed
                remaining_text = f"~{self._format_wall_time(remaining_seconds)}"

        self.conversion_timer.configure(
            text=f"Прошло {elapsed_text}\nОсталось {remaining_text}"
        )

    @staticmethod
    def _format_wall_time(seconds):
        """Форматирует длительность операции как HH:MM:SS."""
        total_seconds = max(0, int(seconds))
        hours, remainder = divmod(total_seconds, 3600)
        minutes, whole_seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}"

    def _conversion_finished(self, success, plan, details, return_code, conversion_seconds):
        """Завершает UI-состояние конвертации, включая отмену пользователем."""
        was_stopped = self._stop_requested
        self._conversion_state = "idle"
        self._conversion_process = None
        self._pause_started_at = None
        self.convert_button.configure(
            state="normal", text="Начать конвертацию", command=self.start_conversion
        )
        self.select_btn.configure(state="normal")
        elapsed_clock = self._format_wall_time(conversion_seconds)
        elapsed_precise = f"{conversion_seconds:.2f}".replace(".", ",")
        elapsed_report = f"{elapsed_clock} ({elapsed_precise} сек.)"

        if self._close_when_finished and not was_stopped:
            self._save_window_state()
            self.destroy()
            return

        if was_stopped:
            # Частично созданный файл намеренно сохраняем: пользователь может
            # самостоятельно решить, оставить его, проверить или удалить.
            self.conversion_status.configure(text="Остановлено")
            self.conversion_timer.configure(text=f"Прошло {elapsed_clock}\nОсталось —")
            if self._close_when_finished:
                self._save_window_state()
                self.destroy()
                return
            message = (
                f"Конвертация остановлена.\n\nВремя работы: {elapsed_report}."
            )
            if os.path.isfile(plan.output_path):
                message += (
                    "\n\nЧастично обработанный файл оставлен на диске:"
                    f"\n{plan.output_path}"
                    "\nОн может быть неполным или не воспроизводиться."
                )
            else:
                message += "\nВыходной файл не был создан."
            messagebox.showinfo("Конвертация остановлена", message, parent=self)
            return

        if success:
            self.progress_bar.set(1)
            self.conversion_status.configure(text="Готово")
            self.conversion_timer.configure(
                text=f"Прошло {elapsed_clock}\nОсталось 00:00:00"
            )
            messagebox.showinfo(
                "Конвертация завершена",
                f"Операция: {plan.summary}\n\n"
                f"Время конвертации: {elapsed_report}\n\n"
                f"Файл сохранён:\n{plan.output_path}",
                parent=self,
            )
        else:
            self.conversion_status.configure(text=f"Ошибка ({return_code})")
            self.conversion_timer.configure(
                text=f"Прошло {elapsed_clock}\nОсталось —"
            )
            messagebox.showerror(
                "Ошибка конвертации",
                f"FFmpeg завершился с кодом {return_code}.\n"
                f"Время до ошибки: {elapsed_report}\n\n"
                f"Последние сообщения:\n{details or 'Нет подробностей.'}",
                parent=self,
            )

    def show_info(self):
        info = self.video_info
        minutes = int(info["duration"] // 60)
        seconds = int(info["duration"] % 60)

        text = (
            f"Разрешение:     {info['width']} × {info['height']}\n"
            f"Длительность:   {minutes} мин {seconds} сек\n"
            f"Битрейт:        {info['bitrate']} кбит/с\n"
            f"Кодек:          {info['codec']}\n"
            f"Тип видео:      {info['type']}"
        )

        self.info_text.configure(state="normal")
        self.info_text.delete("1.0", "end")
        self.info_text.insert("1.0", text)
        self.info_text.configure(state="disabled")

    def show_recommendations(self):
        vtype = self.video_info["type"]
        hw = self.hw_info["type"]
        recommendations = []

        # Сначала обрабатываем неопределённый результат, чтобы не принять
        # слова Half SBS / Half TAB внутри предположения за подтверждённый формат.
        if vtype.startswith("Не определено") or "не уточнён" in vtype or "не определён" in vtype:
            recommendations.append("• Формат 3D не подтверждён — автоматическую конвертацию пока не выбирать")
            recommendations.append("• Проверьте стереоформат вручную: 2D / Half SBS / Full SBS / Half TAB / Full TAB")
            recommendations.append("• Для простой перепаковки в TS без изменения изображения можно вручную выбрать «2D → TS»")
        elif vtype == "Full SBS" or vtype.startswith("Full SBS ("):
            recommendations.append("• Конвертировать Full SBS → Half SBS (если нужна уменьшенная ширина) ")
            recommendations.append("• Либо перепаковать без перекодирования, если менять формат не требуется")
        elif vtype == "Half SBS":
            recommendations.append("• Видео уже в Half SBS — можно перепаковать в .ts / .mp4 без перекодирования")
            recommendations.append("• Либо перекодировать с нужным битрейтом/качеством")
        elif vtype == "Half TAB":
            recommendations.append("• Подтверждён Half TAB (Half Over-Under)")
            recommendations.append("• Для преобразования в SBS: Half TAB → Half SBS")
        elif vtype == "Full TAB":
            recommendations.append("• Подтверждён Full TAB (Full Over-Under)")
            recommendations.append("• Для преобразования в Half SBS потребуется масштабирование и перекомпоновка")
        elif "TAB" in vtype:
            recommendations.append("• Определено расположение Top/Bottom, но Half/Full не уточнён")
            recommendations.append("• Уточните вариант перед выбором параметров конвертации")
        else:
            recommendations.append("• Обычное 2D видео (предположительно)")
            recommendations.append("• Перед 3D-конвертацией проверьте исходный формат")

        recommendations.append("")
        if hw == "nvidia":
            recommendations.append("Рекомендуемый кодировщик: h264_nvenc")
            recommendations.append("Аппаратное кодирование NVIDIA NVENC прошло проверку")
        elif hw == "qsv":
            recommendations.append("Рекомендуемый кодировщик: h264_qsv")
            recommendations.append("Аппаратное кодирование Intel Quick Sync (QSV) прошло проверку")
        else:
            recommendations.append("Аппаратные NVENC/QSV не прошли проверку; конвертация 3D-профилями недоступна")

        text = "\n".join(recommendations)
        self.rec_text.configure(state="normal")
        self.rec_text.delete("1.0", "end")
        self.rec_text.insert("1.0", text)
        self.rec_text.configure(state="disabled")



if __name__ == "__main__":
    app = App()
    app.mainloop()