from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk

from cleaners import DuplicateFinder, utils
from ui import common


class DuplicatesTab(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=8)
        self.finder = DuplicateFinder()
        self.groups: list = []
        self.current_rows: list[dict] = []
        self.auto_checked: set[str] = set()
        self.cancel = None
        self.busy = False
        self._build()

    # ------------------------------------------------------------------
    def _build(self):
        top = ttk.LabelFrame(self, text="Источники поиска", padding=8)
        top.pack(fill="x")
        self.folder_picker = common.FolderPicker(top, label="Папка:",
                                                 initial=os.path.expanduser("~"),
                                                 width=55)
        self.folder_picker.pack(fill="x")
        tip = ttk.Label(top, text="Совет: если папка большая, поиск займёт время. "
                                  "Можно указать несколько путей через ';'.",
                        foreground="#888888")
        tip.pack(anchor="w")

        opts = ttk.Frame(top)
        opts.pack(fill="x", pady=(6, 0))
        self.min_size_var = tk.StringVar(value="0")
        ttk.Label(opts, text="Минимальный размер файла (МБ):").pack(side="left")
        ttk.Entry(opts, textvariable=self.min_size_var, width=7).pack(side="left", padx=4)
        self.ext_var = tk.StringVar(value="")
        ttk.Label(opts, text="Только расширения (jpg,png):").pack(side="left", padx=(12, 0))
        ttk.Entry(opts, textvariable=self.ext_var, width=16).pack(side="left", padx=4)

        self.btn_scan = ttk.Button(opts, text="Найти дубликаты", command=self.scan)
        self.btn_scan.pack(side="right")
        self.btn_cancel = ttk.Button(opts, text="Отмена", command=self.cancel_scan,
                                     state="disabled")
        self.btn_cancel.pack(side="right", padx=4)

        # Таблица результатов
        mid = ttk.LabelFrame(self, text="Найденные дубликаты (группы)", padding=8)
        mid.pack(fill="both", expand=True, pady=6)

        cols = [("check", "", 38), ("path", "Файл", 520), ("size", "Размер", 100),
                ("grp", "Группа", 70)]
        tree_frame = ttk.Frame(mid)
        tree_frame.pack(fill="both", expand=True)
        self.table = common.CheckTable(tree_frame, cols, stretch_col=1, height=16)
        self.table.tree.pack(side="left", fill="both", expand=True)
        ysb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.table.tree.yview)
        ysb.pack(side="right", fill="y")
        self.table.tree.configure(yscrollcommand=ysb.set)

        # ИСПРАВЛЕНО: Динамический пересчет веса выбранных дубликатов при ручных кликах по чекбоксам
        self.table.tree.bind("<ButtonRelease-1>", lambda event: self.after(50, self._update_selection))

        # Нижняя панель
        bottom = ttk.Frame(self)
        bottom.pack(fill="x")
        self.status_var = tk.StringVar(value="Выберите папку и нажмите «Найти дубликаты».")
        ttk.Label(bottom, textvariable=self.status_var, foreground="#666666",
                  wraplength=700).pack(anchor="w")

        btn_row = ttk.Frame(self)
        btn_row.pack(fill="x", pady=(6, 0))
        self.lbl_sel = tk.StringVar(value="Выбрано: 0")
        ttk.Label(btn_row, textvariable=self.lbl_sel).pack(side="left")
        ttk.Button(btn_row, text="Оставить только один (1-й в группе)",
                   command=self.keep_one).pack(side="right")
        ttk.Button(btn_row, text="Удалить выбранные", command=self.delete_selected
                   ).pack(side="right", padx=4)

    # ------------------------------------------------------------------
    def scan(self):
        if self.busy:
            return
        root = self.folder_picker.get()
        if not root:
            common.show_info("Не выбрана папка", "Укажите папку для поиска дубликатов.")
            return
        try:
            min_mb = float(self.min_size_var.get() or "0")
        except ValueError:
            common.show_info("Ошибка ввода", "Минимальный размер должен быть числом (МБ).")
            return
        min_bytes = int(min_mb * 1024 * 1024)
        ext_raw = self.ext_var.get().strip()
        exts = None
        if ext_raw:
            exts = tuple("." + e.strip().lstrip(".") for e in ext_raw.split(",") if e.strip())

        self.finder.min_size = min_bytes
        roots = [p for p in root.split(";") if p.strip()]

        self._set_busy(True)
        self.table.clear()
        self.groups = []
        self.current_rows = []
        self.status_var.set("Сбор файлов…")

        def work(cancel, q):
            def progress(n, name):
                q.put(("progress", f"Обработано файлов: {n} · {os.path.basename(name)}"))
            return self.finder.find(roots, progress_cb=progress, cancel=cancel,
                                    extensions=exts)

        self.cancel = common.run_task(self, work, self._on_scan_msg)

    def _on_scan_msg(self, kind, payload, _cancel):
        if kind == "progress":
            self.status_var.set(payload)
        elif kind == "done":
            self.groups = payload
            self._fill()
        elif kind == "error":
            common.show_error("Ошибка поиска", payload)
            self._finish()

    def _fill(self):
        self.table.clear()
        self.current_rows = []
        self.auto_checked = set()
        total_rows = 0
        freed_potential = 0

        if not self.groups:
            self.status_var.set("Дубликатов не найдено.")
            self._finish()
            return

        for gi, group in enumerate(self.groups, start=1):
            files = sorted(group.files)  
            for fi, path in enumerate(files):
                iid = f"g{gi}-{fi}"
                checked = fi > 0  # Отмечаем галочкой те файлы, которые подлежат УДАЛЕНИЮ (копии)
                if checked:
                    self.auto_checked.add(iid)
                    freed_potential += group.size
                size_txt = utils.human_size(group.size)
                self.table.add_item((iid, path, size_txt, f"№{gi}"), checked=checked)
                self.current_rows.append({"iid": iid, "grp": gi, "path": path,
                                          "size": group.size})
                total_rows += 1

        self._finish()
        self.status_var.set(
            f"Найдено групп: {len(self.groups)} · файлов-копий: {total_rows - len(self.groups)} · "
            f"можно освободить ≈ {utils.human_size(freed_potential)}. Проверьте выбор.")
        self._update_selection()

    # ------------------------------------------------------------------
    def keep_one(self):
        """Оставляет флаг удаления только на копиях, защищая первый файл (оригинал) в группе."""
        by_grp: dict[int, list[dict]] = {}
        for row in self.current_rows:
            by_grp.setdefault(row["grp"], []).append(row)
            
        for gi, rows in by_grp.items():
            rows_sorted = sorted(rows, key=lambda r: r["path"])
            # ИСПРАВЛЕНО: Защищаем самый первый файл (оригинал) — снимаем с него флаг удаления (False), 
            # а на все остальные копии в группе ставим True (пометить к удалению)
            for fi, row in enumerate(rows_sorted):
                self.table.set_check(row["iid"], fi > 0)
                
        self._update_selection()

    def delete_selected(self):
        if self.busy:
            return
        selected = self.table.checked_items()
        if not selected:
            common.show_info("Ничего не выбрано",
                             "Отметьте файлы-копии (или нажмите «Оставить только один (1-й в группе)»).")
            return
        paths = [row["path"] for row in self.current_rows if row["iid"] in selected]
        size = sum(row["size"] for row in self.current_rows if row["iid"] in selected)
        msg = (f"Выбрано файлов: {len(paths)}\n"
               f"Будет освобождено: {utils.human_size(size)}\n\n"
               f"Удалить {len(paths)} файлов?\n"
               "Файлы будут перемещены в корзину.")
        if not common.confirm(self, "Удаление копий", msg):
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
                    common.show_info("Готово", f"Удалено файлов: {len(paths)}\n"
                                               f"Освобождено: {utils.human_size(size)}")
                self._finish()
                self._refresh_after_delete(selected)
            elif kind == "error":
                common.show_error("Ошибка", payload)
                self._finish()

        self.cancel = common.run_task(self, work, on_msg)

    def _refresh_after_delete(self, removed_iids: list[str]):
        self._fill()  
        self.status_var.set("Файлы удалены. Нажмите «Найти дубликаты» для обновления списка.")
        self.groups = []
        self.current_rows = []

        # === Окончание класса DuplicatesTab в файле ui/duplicates_tab.py ===

    def _update_selection(self):
        """Обновляет информацию о количестве и размере выделенных дубликатов."""
        try:
            sel = self.table.checked_items()
            size = sum(r["size"] for r in self.current_rows if r["iid"] in sel)
            self.lbl_sel.set(f"Выбрано: {len(sel)} · {utils.human_size(size)}")
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    def cancel_scan(self):
        """Отменяет текущую асинхронную операцию поиска дубликатов."""
        if self.cancel is not None and not self.cancel.is_set():
            self.cancel.set()
            self.status_var.set("Отмена…")
            self.btn_cancel.config(state="disabled")

    def _finish(self):
        """Сбрасывает состояние занятости интерфейса после завершения потока."""
        self._set_busy(False)
        self.cancel = None

    def _set_busy(self, busy: bool):
        """Блокирует или разблокирует кнопки интерфейса в зависимости от статуса задачи."""
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.btn_scan.config(state=state)
        self.btn_cancel.config(state="normal" if busy else "disabled")
