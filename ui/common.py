"""Общие переиспользуемые виджеты GUI на базе CustomTkinter."""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable

import customtkinter

from cleaners.utils import human_size


# =========================================================================
# FolderPicker
# =========================================================================


class FolderPicker(customtkinter.CTkFrame):
    def __init__(
        self, master, label: str = "Папка:", initial: str = "", width: int = 30
    ):
        super().__init__(master, fg_color="transparent")
        self.lbl = customtkinter.CTkLabel(
            self, text=label, font=("Segoe UI", 10, "bold")
        )
        self.lbl.pack(side="left", padx=(0, 6))

        self.var = tk.StringVar(value=initial)
        self.entry = customtkinter.CTkEntry(
            self,
            textvariable=self.var,
            width=width * 8,
            height=28,
            corner_radius=6,
            border_color="#333333",
        )
        self.entry.pack(side="left", fill="x", expand=True, padx=(0, 4))

        self.btn = customtkinter.CTkButton(
            self,
            text="Обзор...",
            width=60,
            height=28,
            corner_radius=6,
            fg_color="#1e1e1e",
            hover_color="#333333",
            command=self._browse,
        )
        self.btn.pack(side="right")

    def _browse(self):
        from tkinter import filedialog

        path = filedialog.askdirectory(initialdir=self.var.get())
        if path:
            self.var.set(os.path.normpath(path))

    def get(self) -> str:
        return self.var.get().strip()

    def set(self, val: str):
        self.var.set(val)


# =========================================================================
# CheckTable — умная таблица с галочками, сортировкой, фильтром
# =========================================================================


class CheckTable:
    """Treeview с чекбоксами, сортировкой по клику на заголовок,
    текстовым фильтром и фильтром по категориям.

    Данные хранятся в self._data. На диск ничего не пишется.
    """

    SORT_ASC = "asc"
    SORT_DESC = "desc"

    def __init__(
        self,
        master,
        columns: list[tuple[str, str, int]],
        stretch_col: int | None = None,
        height: int = 15,
        *,
        sortable: bool = True,
        kind_labels: dict[str, str] | None = None,
    ):
        self._columns = list(columns)
        self._keys = [c[0] for c in columns]
        self._titles = {c[0]: c[1] for c in columns}
        self._widths = {c[0]: c[2] for c in columns}
        self._has_check_col = bool(self._keys) and self._keys[0] == "check"
        self._sortable = sortable
        self._kind_labels = kind_labels or {}

        self.tree = ttk.Treeview(
            master,
            columns=self._keys,
            show="headings",
            height=height,
            selectmode="extended",
        )

        for i, (key, text, width) in enumerate(columns):
            self.tree.heading(
                key,
                text=text,
                anchor="w",
                command=(lambda k=key: self._on_heading_click(k)) if sortable else "",
            )
            self.tree.column(key, width=width, anchor="w")
            if stretch_col is not None and i == stretch_col:
                self.tree.column(key, stretch=True)

        self.char_on = "☑"
        self.char_off = "☐"

        # Данные
        self._data: list[dict] = []
        self._checked: set[str] = set()
        self._kinds_seen: set[str] = set()

        # Фильтр / сортировка
        self._filter_text: str = ""
        self._kinds_enabled: set[str] = set()  # пусто = все
        self._sort_key: str | None = None
        self._sort_dir: str = self.SORT_ASC

        # Меню ПКМ
        self._context_callback: Callable | None = None

        # Подсветка защищённых
        self.tree.tag_configure("protected", foreground="#ff8844")
        self.tree.tag_configure("normal", foreground="#ffffff")

        # События
        self.tree.bind("<ButtonRelease-1>", self._on_click, add="+")
        self.tree.bind("<Button-3>", self._on_right_click, add="+")

    # ------------------------------------------------------------------
    # Данные
    # ------------------------------------------------------------------

    def clear(self):
        self._data.clear()
        self._checked.clear()
        self._kinds_seen.clear()
        self.tree.delete(*self.tree.get_children())

    def add_item(
        self,
        values: tuple,
        checked: bool = True,
        *,
        kind: str | None = None,
        protected: bool = False,
        sort_keys: dict[str, Any] | None = None,
        extra: dict | None = None,
    ) -> None:
        """values — кортеж; values[0] должен быть iid."""
        iid = str(values[0])
        k = kind or "unknown"
        self._data.append(
            {
                "iid": iid,
                "values": tuple(values),
                "checked": bool(checked) and not protected,
                "kind": k,
                "protected": bool(protected),
                "sort_keys": dict(sort_keys or {}),
                "extra": dict(extra or {}),
                "visible": True,
            }
        )
        self._kinds_seen.add(k)

    def commit(self):
        """Применить фильтр+сортировку и перерисовать."""
        self._reapply()

    # ------------------------------------------------------------------
    # Видимость / фильтры
    # ------------------------------------------------------------------

    def set_filter_text(self, text: str):
        self._filter_text = (text or "").strip().lower()
        self._reapply()

    def set_kinds(self, kinds: set[str]):
        self._kinds_enabled = set(kinds or ())
        self._reapply()

    def kinds_seen(self) -> set[str]:
        return set(self._kinds_seen)

    def _passes_filter(self, d: dict) -> bool:
        if self._kinds_enabled and d["kind"] not in self._kinds_enabled:
            return False
        if self._filter_text:
            hay = " ".join(str(v) for v in d["values"]).lower()
            if self._filter_text not in hay:
                return False
        return True

    # ------------------------------------------------------------------
    # Отрисовка
    # ------------------------------------------------------------------

    def _reapply(self):
        for d in self._data:
            d["visible"] = self._passes_filter(d)

        visible = [d for d in self._data if d["visible"]]
        if self._sort_key is not None:
            col = self._sort_key
            reverse = self._sort_dir == self.SORT_DESC

            def sort_key(d):
                sk = d["sort_keys"]
                if col in sk:
                    return sk[col]
                try:
                    idx = self._keys.index(col)
                except ValueError:
                    return ""
                v = d["values"][idx] if idx < len(d["values"]) else ""
                return str(v).lower()

            try:
                visible.sort(key=sort_key, reverse=reverse)
            except TypeError:
                # на случай смешанных типов — сортируем как строки
                visible.sort(key=lambda d: str(sort_key(d)), reverse=reverse)

        self.tree.delete(*self.tree.get_children())
        for d in visible:
            iid = d["iid"]
            box = self.char_on if iid in self._checked else self.char_off
            values = (box,) + d["values"][1:]
            tag = "protected" if d["protected"] else "normal"
            self.tree.insert("", "end", iid=iid, values=values, tags=(tag,))

        self._update_headings()

    def _update_headings(self):
        for key, title, _w in self._columns:
            text = title
            if key == self._sort_key:
                text = f"{title} {'▲' if self._sort_dir == self.SORT_ASC else '▼'}"
            self.tree.heading(key, text=text)

    # ------------------------------------------------------------------
    # Клики
    # ------------------------------------------------------------------

    def _on_heading_click(self, key: str):
        if key == "check" or not self._sortable:
            return
        if self._sort_key == key:
            self._sort_dir = (
                self.SORT_DESC if self._sort_dir == self.SORT_ASC else self.SORT_ASC
            )
        else:
            self._sort_key = key
            self._sort_dir = self.SORT_ASC
        self._reapply()

    def _on_click(self, event):
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        col = self.tree.identify_column(event.x)
        if col == "#1" and self._has_check_col:
            self.toggle_item(iid)

    def _on_right_click(self, event):
        iid = self.tree.identify_row(event.y)
        if iid and self._context_callback:
            if iid not in self.tree.selection():
                self.tree.selection_set(iid)
            self._context_callback(iid, event.x_root, event.y_root)

    def set_context_callback(self, cb: Callable):
        self._context_callback = cb

    # ------------------------------------------------------------------
    # Чекбоксы и выделение
    # ------------------------------------------------------------------

    def checked_items(self) -> list[str]:
        return [
            d["iid"] for d in self._data if d["iid"] in self._checked and d["visible"]
        ]

    def all_checked_items(self) -> list[str]:
        return list(self._checked)

    def toggle_item(self, iid: str):
        for d in self._data:
            if d["iid"] == iid:
                if d["protected"]:
                    return
                if iid in self._checked:
                    self._checked.remove(iid)
                    box = self.char_off
                else:
                    self._checked.add(iid)
                    box = self.char_on
                vals = self.tree.item(iid, "values")
                if vals:
                    self.tree.item(iid, values=(box,) + vals[1:])
                return

    def set_checked(self, iid: str, value: bool):
        for d in self._data:
            if d["iid"] != iid:
                continue
            if d["protected"] and value:
                return
            if value:
                self._checked.add(iid)
                box = self.char_on
            else:
                self._checked.discard(iid)
                box = self.char_off
            vals = self.tree.item(iid, "values")
            if vals:
                self.tree.item(iid, values=(box,) + vals[1:])
            return

    # ------------------------------------------------------------------
    # Массовые действия по видимым
    # ------------------------------------------------------------------

    def check_all_visible(self):
        for d in self._data:
            if not d["visible"] or d["protected"]:
                continue
            self._checked.add(d["iid"])
        self._reapply()

    def uncheck_all_visible(self):
        for d in self._data:
            if not d["visible"]:
                continue
            self._checked.discard(d["iid"])
        self._reapply()

    def invert_visible(self):
        for d in self._data:
            if not d["visible"] or d["protected"]:
                continue
            iid = d["iid"]
            if iid in self._checked:
                self._checked.discard(iid)
            else:
                self._checked.add(iid)
        self._reapply()

    # ------------------------------------------------------------------
    # Сводка
    # ------------------------------------------------------------------

    def stats(self) -> dict:
        total = len(self._data)
        visible = sum(1 for d in self._data if d["visible"])
        checked = len(self.checked_items())
        size_checked = 0
        for d in self._data:
            if d["iid"] in self._checked and d["visible"] and not d["protected"]:
                sk = d["sort_keys"]
                if "size" in sk:
                    size_checked += int(sk["size"] or 0)
                else:
                    try:
                        idx = self._keys.index("size")
                        size_checked += (
                            int(d["values"][idx]) if idx < len(d["values"]) else 0
                        )
                    except ValueError, TypeError:
                        pass
        return {
            "total": total,
            "visible": visible,
            "checked": checked,
            "size_checked": size_checked,
        }


# =========================================================================
# FilterBar — строка поиска + переключатели категорий + массовые кнопки
# =========================================================================


class FilterBar(customtkinter.CTkFrame):
    def __init__(
        self,
        master,
        table: CheckTable | None = None,
        *,
        kinds: dict[str, str] | None = None,
        on_change: Callable | None = None,
    ):
        super().__init__(master, fg_color="transparent")
        self.table = table
        self._kinds = dict(kinds or {})
        self._on_change = on_change
        self._kind_vars: dict[str, tk.BooleanVar] = {}

        # Верх: поиск
        row1 = customtkinter.CTkFrame(self, fg_color="transparent")
        row1.pack(fill="x", pady=(0, 4))

        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._on_search())
        self.search_entry = customtkinter.CTkEntry(
            row1,
            textvariable=self.search_var,
            placeholder_text="🔎 Поиск по пути, типу, размеру…",
            height=28,
            corner_radius=6,
            border_color="#333333",
        )
        self.search_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))

        customtkinter.CTkButton(
            row1,
            text="✕",
            width=32,
            height=28,
            corner_radius=6,
            fg_color="#1e1e1e",
            hover_color="#333333",
            command=lambda: self.search_var.set(""),
        ).pack(side="left", padx=(0, 6))

        self.lbl_stats = customtkinter.CTkLabel(
            row1, text="", font=("Segoe UI", 10), text_color="#888888"
        )
        self.lbl_stats.pack(side="right")

        # Низ: категории + массовые действия
        row2 = customtkinter.CTkFrame(self, fg_color="transparent")
        row2.pack(fill="x")

        if self._kinds:
            for key, label in self._kinds.items():
                v = tk.BooleanVar(value=True)
                self._kind_vars[key] = v
                customtkinter.CTkCheckBox(
                    row2,
                    text=label,
                    variable=v,
                    font=("Segoe UI", 10),
                    text_color="#cccccc",
                    fg_color="#00ff66",
                    hover_color="#00cc55",
                    checkbox_width=16,
                    checkbox_height=16,
                    command=self._on_kinds,
                ).pack(side="left", padx=(0, 10))

        customtkinter.CTkButton(
            row2,
            text="✓ Все видимые",
            width=110,
            height=24,
            corner_radius=6,
            font=("Segoe UI", 10),
            fg_color="#1e1e1e",
            hover_color="#333333",
            command=self._check_all,
        ).pack(side="right", padx=2)
        customtkinter.CTkButton(
            row2,
            text="✗ Снять все",
            width=100,
            height=24,
            corner_radius=6,
            font=("Segoe UI", 10),
            fg_color="#1e1e1e",
            hover_color="#333333",
            command=self._uncheck_all,
        ).pack(side="right", padx=2)
        customtkinter.CTkButton(
            row2,
            text="⇄ Инвертировать",
            width=120,
            height=24,
            corner_radius=6,
            font=("Segoe UI", 10),
            fg_color="#1e1e1e",
            hover_color="#333333",
            command=self._invert,
        ).pack(side="right", padx=2)

        # Сразу обновляем статистику, только если таблица уже есть
        if self.table is not None:
            self._refresh_stats()

    # ------------------------------------------------------------------

    def attach(self, table: CheckTable):
        """Привязывает реальную таблицу. Обязательно вызвать, если
        FilterBar был создан до CheckTable."""
        self.table = table
        self._refresh_stats()

    # ------------------------------------------------------------------

    def _on_search(self):
        if self.table is None:
            return
        self.table.set_filter_text(self.search_var.get())
        self._refresh_stats()
        if self._on_change:
            self._on_change()

    def _on_kinds(self):
        if self.table is None:
            return
        enabled = {k for k, v in self._kind_vars.items() if v.get()}
        self.table.set_kinds(enabled)
        self._refresh_stats()
        if self._on_change:
            self._on_change()

    def _check_all(self):
        if self.table is None:
            return
        self.table.check_all_visible()
        self._refresh_stats()
        if self._on_change:
            self._on_change()

    def _uncheck_all(self):
        if self.table is None:
            return
        self.table.uncheck_all_visible()
        self._refresh_stats()
        if self._on_change:
            self._on_change()

    def _invert(self):
        if self.table is None:
            return
        self.table.invert_visible()
        self._refresh_stats()
        if self._on_change:
            self._on_change()

    def _refresh_stats(self):
        if self.table is None:
            self.lbl_stats.configure(text="")
            return
        s = self.table.stats()
        self.lbl_stats.configure(
            text=f"Показано: {s['visible']} из {s['total']} · "
            f"выбрано: {s['checked']} ({human_size(s['size_checked'])})"
        )

    def refresh(self):
        self._refresh_stats()

    # ------------------------------------------------------------------

    def _on_search(self):
        self.table.set_filter_text(self.search_var.get())
        self._refresh_stats()
        if self._on_change:
            self._on_change()

    def _on_kinds(self):
        enabled = {k for k, v in self._kind_vars.items() if v.get()}
        self.table.set_kinds(enabled)
        self._refresh_stats()
        if self._on_change:
            self._on_change()

    def _check_all(self):
        self.table.check_all_visible()
        self._refresh_stats()
        if self._on_change:
            self._on_change()

    def _uncheck_all(self):
        self.table.uncheck_all_visible()
        self._refresh_stats()
        if self._on_change:
            self._on_change()

    def _invert(self):
        self.table.invert_visible()
        self._refresh_stats()
        if self._on_change:
            self._on_change()

    def _refresh_stats(self):
        s = self.table.stats()
        self.lbl_stats.configure(
            text=f"Показано: {s['visible']} из {s['total']} · "
            f"выбрано: {s['checked']} ({human_size(s['size_checked'])})"
        )

    def refresh(self):
        self._refresh_stats()


# =========================================================================
# ProfileSelector — пресеты сканирования
# =========================================================================

PROFILES = {
    "quick": {
        "label": "🚀 Быстро (только мусор)",
        "skip_duplicates": True,
        "skip_empty": False,
        "skip_big": False,
        "min_dup_mb": 5.0,
        "big_min_mb": 500.0,
        "top": 100,
    },
    "normal": {
        "label": "⚖ Обычно (сбалансированно)",
        "skip_duplicates": False,
        "skip_empty": False,
        "skip_big": False,
        "min_dup_mb": 1.0,
        "big_min_mb": 100.0,
        "top": 400,
    },
    "deep": {
        "label": "🔍 Глубоко (максимум)",
        "skip_duplicates": False,
        "skip_empty": False,
        "skip_big": False,
        "min_dup_mb": 0.1,
        "big_min_mb": 50.0,
        "top": 800,
    },
    "custom": {
        "label": "⚙ Свои параметры",
        "skip_duplicates": False,
        "skip_empty": False,
        "skip_big": False,
        "min_dup_mb": 1.0,
        "big_min_mb": 100.0,
        "top": 400,
    },
}


class ProfileSelector(customtkinter.CTkFrame):
    def __init__(self, master, *, on_change: Callable | None = None):
        super().__init__(master, fg_color="transparent")
        self._on_change = on_change
        self._key = "normal"

        customtkinter.CTkLabel(
            self,
            text="Профиль:",
            font=("Segoe UI", 10, "bold"),
        ).pack(side="left", padx=(0, 6))

        labels = [v["label"] for v in PROFILES.values()]
        self.combo = customtkinter.CTkComboBox(
            self,
            values=labels,
            width=260,
            height=28,
            corner_radius=6,
            command=self._on_pick,
            dropdown_font=("Segoe UI", 10),
        )
        self.combo.set(PROFILES["normal"]["label"])
        self.combo.pack(side="left")

    def _on_pick(self, choice: str):
        for key, prof in PROFILES.items():
            if prof["label"] == choice:
                self._key = key
                break
        if self._on_change:
            self._on_change(self.get_options())

    def get_key(self) -> str:
        return self._key

    def get_options(self) -> dict:
        return dict(PROFILES[self._key])


# =========================================================================
# Фоновые задачи
# =========================================================================


class AppTaskCancelToken:
    def __init__(self):
        self._flag = threading.Event()

    def set(self):
        self._flag.set()

    def is_set(self) -> bool:
        return self._flag.is_set()


class TaskHandle:
    def __init__(self):
        self.cancel = AppTaskCancelToken()
        self.done = threading.Event()
        self._alive = True
        self.thread: threading.Thread | None = None

    def stop(self):
        self.cancel.set()


def run_task(master, worker_func, callback_cb) -> TaskHandle:
    handle = TaskHandle()
    q: queue.Queue = queue.Queue()

    def worker():
        try:
            result = worker_func(handle.cancel, q)
            q.put(("__done__", result))
        except BaseException as e:
            q.put(("__error__", f"{type(e).__name__}: {e}"))
        finally:
            handle.done.set()

    def poll():
        if not handle._alive:
            return
        finished = False
        try:
            while True:
                try:
                    kind, payload = q.get_nowait()
                except queue.Empty:
                    break
                if kind == "__done__":
                    callback_cb("done", payload, handle.cancel)
                    finished = True
                    break
                if kind == "__error__":
                    callback_cb("error", payload, handle.cancel)
                    finished = True
                    break
                callback_cb(kind, payload, handle.cancel)
        except Exception:
            pass
        if finished:
            handle._alive = False
            return
        try:
            master.after(80, poll)
        except tk.TclError:
            handle._alive = False

    handle.thread = threading.Thread(target=worker, daemon=True, name="DiskCleanerTask")
    handle.thread.start()
    try:
        master.after(80, poll)
    except tk.TclError:
        handle._alive = False
    return handle


# =========================================================================
# PreviewDialog (как в волне 1)
# =========================================================================


class PreviewDialog(customtkinter.CTkToplevel):
    def __init__(self, master, preview, title: str = "Предпросмотр удаления"):
        super().__init__(master)
        self.result = False
        self.title(title)
        self.geometry("820x600")
        self.minsize(600, 400)
        self.transient(master)
        self.after(50, self.grab_set)

        head = customtkinter.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(14, 4))
        customtkinter.CTkLabel(
            head,
            text=f"Объектов к удалению: {len(preview.items)}",
            font=("Segoe UI", 14, "bold"),
            text_color="#00ff66",
        ).pack(anchor="w")
        customtkinter.CTkLabel(
            head,
            text=f"Освободится места: {human_size(preview.total_size)}",
            font=("Segoe UI", 12),
            text_color="#cccccc",
        ).pack(anchor="w", pady=(2, 0))

        if preview.protected_count:
            warn = customtkinter.CTkFrame(
                self,
                corner_radius=6,
                fg_color="#3a2a00",
                border_width=1,
                border_color="#8a6a00",
            )
            warn.pack(fill="x", padx=16, pady=(8, 4))
            customtkinter.CTkLabel(
                warn,
                text=(
                    f"⚠️ {preview.protected_count} объектов "
                    f"({human_size(preview.protected_size)}) помечены как защищённые "
                    f"или находятся в белом списке. Они НЕ будут удалены."
                ),
                font=("Segoe UI", 11),
                text_color="#ffcc66",
                wraplength=760,
                justify="left",
            ).pack(padx=10, pady=8, anchor="w")

        tbl = customtkinter.CTkFrame(
            self, corner_radius=6, border_width=1, border_color="#333333"
        )
        tbl.pack(fill="both", expand=True, padx=16, pady=6)
        cols = ("path", "size", "note")
        self.tree = ttk.Treeview(tbl, columns=cols, show="headings", height=14)
        for key, text, width in (
            ("path", "Объект", 480),
            ("size", "Размер", 90),
            ("note", "Примечание", 200),
        ):
            self.tree.heading(key, text=text, anchor="w")
            self.tree.column(key, width=width, anchor="w")
        self.tree.pack(side="left", fill="both", expand=True, padx=4, pady=4)
        ysb = ttk.Scrollbar(tbl, orient="vertical", command=self.tree.yview)
        ysb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=ysb.set)

        for item in preview.items:
            note = "УДАЛИТЬ" if not item.protected else (item.reason or "защищён")
            tag = "protected" if item.protected else "normal"
            self.tree.insert(
                "", "end", values=(item.path, human_size(item.size), note), tags=(tag,)
            )
        self.tree.tag_configure("protected", foreground="#ff8844")
        self.tree.tag_configure("normal", foreground="#ffffff")

        row = customtkinter.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(4, 14))
        customtkinter.CTkButton(
            row,
            text="Отмена",
            width=130,
            height=34,
            fg_color="#1e1e1e",
            hover_color="#333333",
            command=self._cancel,
        ).pack(side="right", padx=4)
        customtkinter.CTkButton(
            row,
            text="🗑 Удалить",
            width=160,
            height=34,
            fg_color="#00ff66",
            hover_color="#00cc55",
            text_color="#000000",
            font=("Segoe UI", 12, "bold"),
            command=self._confirm,
        ).pack(side="right", padx=4)

        self.bind("<Escape>", lambda e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)

    def _confirm(self):
        self.result = True
        self.destroy()

    def _cancel(self):
        self.result = False
        self.destroy()


# =========================================================================
# MessageBox-хелперы
# =========================================================================


def show_info(title: str, msg: str):
    from tkinter import messagebox

    messagebox.showinfo(title, msg)


def show_error(title: str, msg: str):
    from tkinter import messagebox

    messagebox.showerror(title, msg)


def confirm(master, title: str, msg: str) -> bool:
    from tkinter import messagebox

    return messagebox.askyesno(title, msg)
