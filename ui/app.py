"""Главное окно приложения: Notebook с вкладками."""
from __future__ import annotations

import os
import sys
import tkinter as tk
from tkinter import ttk

from ui.fullcheck_tab import FullCheckTab
from ui.system_tab import SystemTab
from ui.duplicates_tab import DuplicatesTab
from ui.bigfiles_tab import BigFilesTab
from ui.emptyfolders_tab import EmptyFoldersTab
from ui.drivers_tab import DriversTab
from ui.driver_auto_tab import DriverAutoTab

APP_TITLE = "🧹 Очиститель и анализатор диска"
APP_SIZE = "1100x760"

# Индексы вкладок в Notebook
TAB_INDEX = {
    "fullcheck": 0,
    "system": 1,
    "duplicates": 2,
    "bigfiles": 3,
    "empty": 4,
    "drivers": 5,
    "driver_auto": 6,
}


def _parse_folder_arg(argv: list[str] | None) -> str:
    """Возвращает путь папки из аргументов командной строки (для контекстного меню)."""
    args = list(argv) if argv is not None else list(sys.argv[1:])
    if not args:
        return ""
    raw = args[0].strip().strip('"')
    if os.path.isdir(raw):
        return os.path.normpath(raw)
    return ""


def run(argv: list[str] | None = None) -> None:
    folder = _parse_folder_arg(argv)

    root = tk.Tk()
    root.title(APP_TITLE)
    root.geometry(APP_SIZE)
    root.minsize(900, 640)

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    nb = ttk.Notebook(root)
    nb.pack(fill="both", expand=True, padx=4, pady=4)

    classes = [FullCheckTab, SystemTab, DuplicatesTab, BigFilesTab,
               EmptyFoldersTab, DriversTab, DriverAutoTab]
    tab_frames = []
    titles = ["⚡ Полная проверка", "🧹 Очистка системы", "🔁 Дубликаты",
              "🗜 Большие файлы", "📂 Пустые папки", "🛠 Драйверы",
              "🔄 Автообновление"]
    for text, cls in zip(titles, classes):
        frame = cls(nb)
        nb.add(frame, text=text)
        tab_frames.append(frame)

    # Если приложение вызвано из контекстного меню с путём — переходим
    # на вкладку «Большие файлы» и запускаем сканирование этой папки.
    if folder:
        nb.select(TAB_INDEX["bigfiles"])
        big = tab_frames[TAB_INDEX["bigfiles"]]
        big.folder_picker.set(folder)
        root.after(300, big.scan)

    root.mainloop()


if __name__ == "__main__":
    run()
