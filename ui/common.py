"""Общие виджеты и фоновые задачи для интерфейса приложения."""
from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from cleaners import utils


# --------------------------------------------------------------------------
# Фоновые задачи
# --------------------------------------------------------------------------

def run_task(root: tk.Misc, func, handler) -> threading.Event:
    """Запускает func(cancel: Event, q: Queue) в фоновом потоке.

    handler(kind, payload, cancel) вызывается в GUI-потоке,
    kind: 'progress' | 'done' | 'error'.

    Если поток завершился с результатом — кладёт ("done", result),
    при исключении — ("error", text). При отмене (cancel.is_set())
    результат "done" НЕ кладётся — вкладка сама обработает отмену.

    Возвращает объект Event, через который операцию можно отменить.
    """
    cancel = threading.Event()
    q: queue.Queue = queue.Queue()

    def target():
        try:
            result = func(cancel, q)
            if not cancel.is_set():
                q.put(("done", result))
        except Exception as exc:  # noqa: BLE001
            q.put(("error", f"{type(exc).__name__}: {exc}"))

    thread = threading.Thread(target=target, daemon=True)
    thread.start()

    def poll():
        try:
            while True:
                item = q.get_nowait()
                handler(item[0], item[1], cancel)
        except queue.Empty:
            pass
        if thread.is_alive():
            root.after(120, poll)

    poll()
    return cancel


# --------------------------------------------------------------------------
# Диалоги и подтверждения
# --------------------------------------------------------------------------

def ask_directory(parent: tk.Misc, title: str = "Выберите папку",
                  initial: str = "") -> str:
    d = filedialog.askdirectory(parent=parent, title=title,
                                initialdir=initial or os.path.expanduser("~"))
    return d or ""


def show_info(title: str, message: str) -> None:
    messagebox.showinfo(title, message)


def show_error(title: str, message: str) -> None:
    messagebox.showerror(title, message)


def confirm(parent: tk.Misc, title: str, message: str) -> bool:
    return messagebox.askyesno(title, message, parent=parent)


# --------------------------------------------------------------------------
# Виджет выбора папки
# --------------------------------------------------------------------------

class FolderPicker(ttk.Frame):
    """Строка «Папка: [поле] [Обзор…]»."""

    def __init__(self, master, label: str = "Папка:", initial: str = "",
                 width: int = 60):
        super().__init__(master)
        self.var = tk.StringVar(value=initial)
        ttk.Label(self, text=label).pack(side="left")
        self.entry = ttk.Entry(self, textvariable=self.var, width=width)
        self.entry.pack(side="left", fill="x", expand=True, padx=(4, 4))
        ttk.Button(self, text="Обзор…", command=self._browse).pack(side="left")

    def _browse(self):
        d = ask_directory(self, initial=self.get())
        if d:
            self.var.set(d)

    def get(self) -> str:
        return self.var.get().strip().strip('"')

    def set(self, path: str):
        self.var.set(path)


# --------------------------------------------------------------------------
# Таблица с галочками (первая колонка — символ ☑/☐)
# --------------------------------------------------------------------------

class CheckTable:
    """ttk.Treeview с первой колонкой-галочкой.

    Строки добавляются через add_item(row, checked); row[0] — уникальный id.
    Клик по первой колонке или пробел переключает галочку.
    """

    def __init__(self, parent, columns, stretch_col: int = 1, height: int = 14):
        # columns: [(cid, heading, width_px), ...]; первая колонка зарезервирована
        self.columns = columns
        self.tree = ttk.Treeview(parent, columns=[c[0] for c in columns],
                                 show="headings", selectmode="extended", height=height)
        for i, (cid, text, width) in enumerate(columns):
            self.tree.heading(cid, text=text)
            anchor = "center" if i == 0 else "w"
            self.tree.column(cid, width=width, anchor=anchor,
                             stretch=(i == stretch_col))
        self.checked: dict[str, bool] = {}
        self._rows: list[str] = []
        self.tree.bind("<Button-1>", self._on_click)
        self.tree.bind("<space>", self._on_space)

    # --- наполнение ----------------------------------------------------
    def set_items(self, items: list[tuple], checked: bool = True):
        self.clear()
        for row in items:
            self.add_item(row, checked)

    def add_item(self, row: tuple, checked: bool = True):
        iid = row[0]
        values = [row[i] for i in range(1, len(row))]
        values.insert(0, "☑" if checked else "☐")
        self.tree.insert("", "end", iid=iid, values=values)
        self.checked[iid] = checked
        self._rows.append(iid)

    def clear(self):
        self.tree.delete(*self.tree.get_children())
        self.checked.clear()
        self._rows.clear()

    # --- состояние ------------------------------------------------------
    def is_checked(self, iid: str) -> bool:
        return self.checked.get(iid, False)

    def checked_items(self) -> list[str]:
        return [iid for iid in self._rows if self.checked.get(iid)]

    def count_checked(self) -> int:
        return len(self.checked_items())

    # --- переключение ----------------------------------------------------
    def toggle(self, iid: str):
        if iid not in self.checked:
            return
        self.checked[iid] = not self.checked[iid]
        vals = list(self.tree.item(iid, "values"))
        vals[0] = "☑" if self.checked[iid] else "☐"
        self.tree.item(iid, values=vals)

    def toggle_all(self, state: bool):
        for iid in self._rows:
            self.checked[iid] = state
            vals = list(self.tree.item(iid, "values"))
            vals[0] = "☑" if state else "☐"
            self.tree.item(iid, values=vals)

    def set_check(self, iid: str, state: bool):
        if iid not in self.checked:
            return
        self.checked[iid] = state
        vals = list(self.tree.item(iid, "values"))
        vals[0] = "☑" if state else "☐"
        self.tree.item(iid, values=vals)

    # --- события ---------------------------------------------------------
    def _on_click(self, event):
        col = self.tree.identify_column(event.x)
        if col == "#1":
            iid = self.tree.identify_row(event.y)
            if iid:
                self.toggle(iid)
                return "break"
        return None

    def _on_space(self, _event):
        iid = self.tree.focus()
        if iid:
            self.toggle(iid)
            return "break"
        return None
