"""Вкладка «Пустые папки»: поиск и удаление пустых каталогов."""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk

from cleaners import find_empty_folders, utils
from ui import common


class EmptyFoldersTab(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=8)
        self.rows: list[dict] = []
        self.cancel = None
        self.busy = False
        self._build()

    def _build(self):
        top = ttk.LabelFrame(self, text="Параметры", padding=8)
        top.pack(fill="x")
        self.folder_picker = common.FolderPicker(top, label="Папка:",
                                                 initial=os.path.expanduser("~"),
                                                 width=55)
        self.folder_picker.pack(fill="x")
        opts = ttk.Frame(top)
        opts.pack(fill="x", pady=(6, 0))
        self.min_depth_var = tk.StringVar(value="1")
        ttk.Label(opts, text="Не считать пустыми папки ближе, чем на уровней от корня:").pack(side="left")
        ttk.Entry(opts, textvariable=self.min_depth_var, width=4).pack(side="left", padx=4)
        self.btn_scan = ttk.Button(opts, text="Найти пустые папки", command=self.scan)
        self.btn_scan.pack(side="right")
        self.btn_cancel = ttk.Button(opts, text="Отмена", command=self.cancel_scan,
                                     state="disabled")
        self.btn_cancel.pack(side="right", padx=4)

        mid = ttk.LabelFrame(self, text="Пустые папки", padding=8)
        mid.pack(fill="both", expand=True, pady=6)
        cols = [("check", "", 38), ("path", "Папка", 600)]
        tf = ttk.Frame(mid)
        tf.pack(fill="both", expand=True)
        self.table = common.CheckTable(tf, cols, stretch_col=1, height=16)
        self.table.tree.pack(side="left", fill="both", expand=True)
        ysb = ttk.Scrollbar(tf, orient="vertical", command=self.table.tree.yview)
        ysb.pack(side="right", fill="y")
        self.table.tree.configure(yscrollcommand=ysb.set)

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", pady=(6, 0))
        self.status_var = tk.StringVar(
            value="Пустой считается папка, внутри которой нет ни одного файла "
                  "(включая вложенные). Удаление — в корзину.")
        ttk.Label(bottom, textvariable=self.status_var, foreground="#666666",
                  wraplength=760, justify="left").pack(anchor="w")
        row2 = ttk.Frame(self)
        row2.pack(fill="x", pady=(4, 0))
        self.lbl_sel = tk.StringVar(value="Выбрано: 0")
        ttk.Label(row2, textvariable=self.lbl_sel).pack(side="left")
        self.btn_delete = ttk.Button(row2, text="Удалить выбранные", command=self.delete_selected,
                                     state="disabled")
        self.btn_delete.pack(side="right")

    # ------------------------------------------------------------------
    def scan(self):
        if self.busy:
            return
        root = self.folder_picker.get()
        if not root:
            common.show_info("Не выбрана папка", "Укажите папку для поиска.")
            return
        try:
            min_depth = int(self.min_depth_var.get() or "1")
        except ValueError:
            common.show_info("Ошибка ввода", "Значение глубины должно быть целым числом.")
            return
        min_depth = max(0, min_depth)

        self._set_busy(True)
        self.table.clear()
        self.rows = []

        def work(cancel, q):
            def progress(_n, name):
                q.put(("progress", f"Проверка: {name}"))
            return find_empty_folders(root, progress_cb=progress, cancel=cancel,
                                      min_depth=min_depth)

        self.cancel = common.run_task(self, work, self._on_msg)

    def _on_msg(self, kind, payload, _cancel):
        if kind == "progress":
            self.status_var.set(payload)
        elif kind == "done":
            self._fill(payload)
        elif kind == "error":
            common.show_error("Ошибка сканирования", payload)
            self._finish()

    def _fill(self, empty_folders):
        self.table.clear()
        self.rows = []
        if not empty_folders:
            self.status_var.set("Пустых папок не найдено.")
            self._finish()
            return
        for i, ef in enumerate(empty_folders):
            iid = f"ef{i}"
            self.table.add_item((iid, ef.path), checked=True)
            self.rows.append({"iid": iid, "path": ef.path})
        self._finish()
        self.status_var.set(f"Найдено пустых папок: {len(empty_folders)}. "
                            "Отметьте и удалите ненужные (можно восстановить из корзины).")
        self.btn_delete.config(state="normal")
        self._update_sel()

    # ------------------------------------------------------------------
    def delete_selected(self):
        if self.busy:
            return
        selected = self.table.checked_items()
        if not selected:
            common.show_info("Ничего не выбрано", "Отметьте папки для удаления.")
            return
        paths = [r["path"] for r in self.rows if r["iid"] in selected]
        # проверяем, что папки реально пустые (могли появиться файлы)
        non_empty = [p for p in paths if os.path.exists(p) and os.listdir(p)]
        if non_empty:
            common.show_error("Операция отменена",
                              "Следующие папки уже не пустые (в них появились файлы):\n" +
                              "\n".join(non_empty[:8]))
            return
        msg = (f"Удалить пустых папок: {len(paths)}?\n\n"
               "Папки будут перемещены в корзину.")
        if not common.confirm(self, "Удаление пустых папок", msg):
            return

        self._set_busy(True)
        self.status_var.set("Удаление…")

        def work(cancel, q):
            return utils.move_to_recycle(paths)

        def on_msg(kind, payload, _cancel):
            if kind == "done":
                bad = payload
                if bad:
                    common.show_error("Не все папки удалены",
                                      f"Не удалось: {len(bad)}\n" +
                                      "\n".join(os.path.basename(b) for b in bad[:8]))
                else:
                    common.show_info("Готово", f"Удалено папок: {len(paths)}")
                self._finish()
                self._refresh(bad)
            elif kind == "error":
                common.show_error("Ошибка", payload)
                self._finish()

        self.cancel = common.run_task(self, work, on_msg)

    def _refresh(self, bad_paths: list[str]):
        # удалённые убираем; неудачные остаются
        self.rows = [r for r in self.rows if r["path"] not in bad_paths]
        self.table.clear()
        for i, r in enumerate(self.rows):
            self.table.add_item((f"ef{i}", r["path"]), checked=True)
            r["iid"] = f"ef{i}"
        self._update_sel()
        if not self.rows:
            self.btn_delete.config(state="disabled")
            self.status_var.set("Пустых папок не осталось.")

    def _update_sel(self):
        sel = self.table.checked_items()
        self.lbl_sel.set(f"Выбрано: {len(sel)}")

    # ------------------------------------------------------------------
    def cancel_scan(self):
        if self.cancel is not None and not self.cancel.is_set():
            self.cancel.set()
            self.status_var.set("Отмена…")
            self.btn_cancel.config(state="disabled")

    def _finish(self):
        self._set_busy(False)
        self.cancel = None

    def _set_busy(self, busy: bool):
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.btn_scan.config(state=state)
        self.btn_cancel.config(state="normal" if busy else "disabled")
