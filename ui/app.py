"""Главный оркестратор графической оболочки на базе фреймворка CustomTkinter."""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import traceback
from tkinter import ttk

import customtkinter

from cleaners.utils import setup_app_logging
from ui.analyzer_tab import AnalyzerTab
from ui.fullcheck_tab import FullCheckTab
from ui.registry_tab import RegistryTab
from ui.startup_tab import StartupTab
from ui.uninstaller_tab import UninstallerTab

APP_TITLE = "🧹 DiskCleaner, Drivers & Force Uninstaller v3.0.0"
APP_SIZE = "1180x760"

# Корень проекта = папка, где лежит main.py = родитель каталога ui/
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# =========================================================================
# Пути к ресурсам (работают из исходников и из PyInstaller onefile)
# =========================================================================


def _resource_path(rel: str) -> str:
    """Путь к ресурсу.
    - Из исходников: <project_root>/<rel>
    - Из PyInstaller onefile: <_MEIPASS>/<rel>
    """
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, rel)
    return os.path.join(_PROJECT_ROOT, rel)


ASSETS_DIR = _resource_path("assets")


# =========================================================================
# DPI-awareness (обязательно ДО создания root — иначе мыльные шрифты на 4K)
# =========================================================================


def _enable_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-Monitor V2
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


# =========================================================================
# Иконка приложения
# =========================================================================


def _set_app_icon(root) -> None:
    ico = os.path.join(ASSETS_DIR, "icon.ico")
    if os.path.isfile(ico):
        try:
            root.iconbitmap(ico)
        except Exception as e:
            logging.warning(f"Не удалось поставить иконку: {e}")


# =========================================================================
# Аргументы командной строки (контекстное меню Explorer)
# =========================================================================


def _parse_folder_arg(argv: list[str] | None) -> str:
    args = list(argv) if argv is not None else sys.argv[1:]
    if not args:
        return ""
    raw = args[0].strip().strip('"') if args else ""
    if os.path.isdir(raw):
        return os.path.normpath(raw)
    return ""


# =========================================================================
# Стиль Treeview (тёмная тема)
# =========================================================================


def _configure_treeview_style() -> None:
    style = ttk.Style()
    style.theme_use("clam")
    style.configure(
        "Treeview",
        background="#252525",
        foreground="#ffffff",
        fieldbackground="#252525",
        rowheight=26,
        borderwidth=0,
    )
    style.configure(
        "Treeview.Heading",
        background="#1e1e1e",
        foreground="#00ff66",
        bordercolor="#333333",
        borderwidth=1,
        font=("Segoe UI", 10, "bold"),
    )
    style.map(
        "Treeview",
        background=[("selected", "#1b5e20")],
        foreground=[("selected", "#ffffff")],
    )


# =========================================================================
# Главный вход
# =========================================================================


def run(argv: list[str] | None = None) -> None:
    setup_app_logging()
    _enable_dpi_awareness()
    logging.info("=== GUI запускается ===")

    folder = _parse_folder_arg(argv)

    customtkinter.set_appearance_mode("dark")
    customtkinter.set_default_color_theme("green")

    root = customtkinter.CTk()
    root.title(APP_TITLE)
    _set_app_icon(root)

    # Адаптивный размер: не больше экрана, с запасом под панель задач
    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()
    win_w = min(1280, max(900, screen_w - 120))
    win_h = min(820, max(600, screen_h - 120))
    root.geometry(
        f"{win_w}x{win_h}+{(screen_w - win_w) // 2}+{max(0, (screen_h - win_h) // 2 - 20)}"
    )
    root.minsize(860, 560)

    _configure_treeview_style()

    tabview = customtkinter.CTkTabview(
        root,
        segmented_button_selected_color="#00ff66",
        segmented_button_selected_hover_color="#00cc55",
        segmented_button_unselected_color="#1e1e1e",
        text_color="#ffffff",
    )
    tabview.pack(fill="both", expand=True, padx=10, pady=10)

    tab_optimization = tabview.add("📊 Анализ и Очистка Диска")
    tab_analyzer = tabview.add("💾 Анализатор места")
    tab_startup = tabview.add("⚡ Автозагрузка")
    tab_registry = tabview.add("🗄 Реестр")
    tab_uninstaller = tabview.add("📦 Деинсталлятор")

    optimization_frame = FullCheckTab(tab_optimization)
    optimization_frame.pack(fill="both", expand=True)

    from ui.analyzer_tab import AnalyzerTab

    analyzer_frame = AnalyzerTab(tab_analyzer)
    analyzer_frame.pack(fill="both", expand=True)

    from ui.startup_tab import StartupTab

    startup_frame = StartupTab(tab_startup)
    startup_frame.pack(fill="both", expand=True)

    from ui.registry_tab import RegistryTab

    registry_frame = RegistryTab(tab_registry)
    registry_frame.pack(fill="both", expand=True)

    uninstaller_frame = UninstallerTab(tab_uninstaller)
    uninstaller_frame.pack(fill="both", expand=True)

    if folder:
        tabview.set("📊 Анализ и Очистка Диска")
        optimization_frame.picker.set(folder)
        root.after(400, optimization_frame.run_check)

    # Глобальный обработчик исключений Tk — окно не умирает молча
    def _tk_exception_handler(exc, val, tb):
        msg = "".join(traceback.format_exception(exc, val, tb))
        logging.error("Необработанное исключение Tk:\n%s", msg)
        try:
            from tkinter import messagebox

            messagebox.showerror(
                "Ошибка",
                f"{exc.__name__}: {val}\n\nПодробности — в логе.",
            )
        except Exception:
            pass

    root.report_callback_exception = _tk_exception_handler

    try:
        root.mainloop()
    except KeyboardInterrupt:
        logging.info("Завершение по Ctrl+C")
    finally:
        logging.info("=== GUI закрыт ===")


if __name__ == "__main__":
    run()
