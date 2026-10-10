"""Вкладка «Большие файлы»: топ крупнейших файлов в выбранной папке/диске."""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk

from cleaners import find_big_files, utils
from ui import common


class BigFilesTab(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=8)
        self.rows: list[dict] = []
        self.cancel = None
        self.busy = False
        self._build()

    def _build(self):
        top = ttk.LabelFrame(self, text="Параметры сканирования", padding=8)
        top.pack(fill="x")
        self.folder_picker = common.FolderPicker(top, label="Папка:",
                                                 initial=os.path.expanduser("~"),
                                                 width=55)
        self.folder_picker.pack(fill="x")

        opts = ttk.Frame(top)
        opts.pack(fill="x", pady=(6, 0))
        self.min_mb_var = tk.StringVar(value="100")
        ttk.Label(opts, text="Искать файлы больше (МБ):").pack(side="left")
        ttk.Entry(opts, textvariable=self.min_mb_var, width=8).pack(side="left", padx=4)
        self.top_var = tk.StringVar(value="200")
        ttk.Label(opts, text="Показывать топ:").pack(side="left", padx=(14, 0))
        ttk.Entry(opts, textvariable=self.top_var, width=6).pack(side="left", padx=4)

        self.btn_scan = ttk.Button(opts, text="Сканировать", command=self.scan)
        self.btn_scan.pack(side="right")
        self.btn_cancel = ttk.Button(opts, text="Отмена", command=self.cancel_scan,
                                     state="disabled")
        self.btn_cancel.pack(side="right", padx=4)

        # Таблица результатов
        mid = ttk.LabelFrame(self, text="Крупные файлы", padding=8)
        mid.pack(fill="both", expand=True, pady=6)
        cols = [("check", "", 38), ("path", "Файл", 560), ("size", "Размер", 110),
                ("mtime", "Изменён", 130)]
        tf = ttk.Frame(mid)
        tf.pack(fill="both", expand=True)
        self.table = common.CheckTable(tf, cols, stretch_col=1, height=16)
        self.table.tree.pack(side="left", fill="both", expand=True)
        ysb = ttk.Scrollbar(tf, orient="vertical", command=self.table.tree.yview)
        ysb.pack(side="right", fill="y")
        self.table.tree.configure(yscrollcommand=ysb.set)

        # ИСПРАВЛЕНО: Связываем клик по чекбоксу с мгновенным обновлением веса
        self.table.tree.bind("<ButtonRelease-1>", lambda event: self.after(50, self._update_sel))

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", pady=(6, 0))
        self.status_var = tk.StringVar(value="Укажите папку или диск и нажмите «Сканировать».")
        ttk.Label(bottom, textvariable=self.status_var, foreground="#666666",
                  wraplength=700).pack(anchor="w")
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
            common.show_info("Не выбрана папка", "Укажите папку или диск.")
            return
        try:
            min_mb = float(self.min_mb_var.get() or "0")
            top = int(self.top_var.get() or "200")
        except ValueError:
            common.show_info("Ошибка ввода", "Проверьте числовые значения.")
            return
        min_bytes = int(min_mb * 1024 * 1024)

        self._set_busy(True)
        self.table.clear()
        self.rows = []

        def work(cancel, q):
            def progress(n, name):
                q.put(("progress", f"Просмотрено файлов: {n} · {name}"))
            return find_big_files(root, min_bytes, top=top,
                                  progress_cb=progress, cancel=cancel)

        self.cancel = common.run_task(self, work, self._on_msg)

    def _on_msg(self, kind, payload, _cancel):
        if kind == "progress":
            self.status_var.set(payload)
        elif kind == "done":
            self._fill(payload)
        elif kind == "error":
            common.show_error("Ошибка сканирования", payload)
            self._finish()

    def _fill(self, big_files):
        self.table.clear()
        self.rows = []
        if not big_files:
            self.status_var.set("Крупных файлов не найдено (или папка пуста).")
            self._finish()
            return
        for i, bf in enumerate(big_files):
            iid = f"big{i}"
            mtime = "—"
            try:
                import time
                mtime = time.strftime("%d.%m.%Y %H:%M",
                                      time.localtime(os.path.getmtime(bf.path)))
            except OSError:
                pass
            self.table.add_item((iid, bf.path, utils.human_size(bf.size), mtime),
                                checked=True)
            # ИСПРАВЛЕНО: mtime теперь надежно кэшируется в ОЗУ
            self.rows.append({"iid": iid, "path": bf.path, "size": bf.size, "mtime": mtime})
            
        self._finish()
        self.status_var.set(
            f"Показано файлов: {len(big_files)}. "
            "Отметьте ненужные и нажмите «Удалить выбранные».")
        self.btn_delete.config(state="normal")
        self._update_sel()

    # ------------------------------------------------------------------
    def delete_selected(self):
        if self.busy:
            return
        selected = self.table.checked_items()
        if not selected:
            common.show_info("Ничего не выбрано", "Отметьте файлы для удаления.")
            return
        paths = [r["path"] for r in self.rows if r["iid"] in selected]
        size = sum(r["size"] for r in self.rows if r["iid"] in selected)
        msg = (f"Выбрано: {len(paths)} файлов\n"
               f"Будет освобождено: {utils.human_size(size)}\n\n"
               "Удалить выбранные файлы (в корзину)?")
        if not common.confirm(self, "Удаление файлов", msg):
            return

        self._set_busy(True)
        self.status_var.set("Удаление…")

        def work(cancel, q):
            return utils.move_to_recycle(paths)

        def on_msg(kind, payload, _cancel):
            if kind == "done":
                bad = payload
                if bad:
                    common.show_error("Не все файлы удалены",
                                      f"Не удалось: {len(bad)}\n" +
                                      "\n".join(os.path.basename(b) for b in bad[:8]))
                else:
                    common.show_info("Готово", f"Удалено: {len(paths)} файлов\n"
                                               f"Освобождено: {utils.human_size(size)}")
                self._finish()
                # Убираем успешно удалённые из памяти
                self.rows = [r for r in self.rows if r["path"] not in bad]
                self._fill_from_rows()
            elif kind == "error":
                common.show_error("Ошибка", payload)
                self._finish()

        self.cancel = common.run_task(self, work, on_msg)

    def _fill_from_rows(self):
        """Пересобирает таблицу из оставшихся rows без потери дат изменения."""
        self.table.clear()
        for r in self.rows:
            # ИСПРАВЛЕНО: Передается сохраненное r["mtime"] вместо затирания пустой строкой
            self.table.add_item((r["iid"], r["path"], utils.human_size(r["size"]), r["mtime"]),
                                checked=True)
        self._update_sel()
        if not self.rows:
            self.btn_delete.config(state="disabled")
            self.status_var.set("Список пуст.")

    def _update_sel(self):
        try:
            sel = self.table.checked_items()
            size = sum(r["size"] for r in self.rows if r["iid"] in sel)
            self.lbl_sel.set(f"Выбрано: {len(sel)} · {utils.human_size(size)}")
        except tk.TclError:
            pass

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
