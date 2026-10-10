"""Вкладка «Очистка системы»: категории мусора, сканирование, очистка."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from cleaners import SystemCleaner, utils
from ui import common


class SystemTab(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=8)
        self.cleaner = SystemCleaner()
        self.report: list[dict] = []
        self.cancel = None
        self.busy = False
        self._build()

    # ------------------------------------------------------------------
    def _build(self):
        header = ttk.Label(
            self,
            text="Временные файлы, кэш приложений и браузеров, корзина.\n"
                 "Файлы перемещаются в корзину Windows (можно восстановить).",
            justify="left")
        header.pack(anchor="w")

        # --- Таблица категорий ---
        columns = [("check", "", 40), ("title", "Категория", 300),
                   ("size", "Размер", 110), ("desc", "Где находится / примечание", 420)]
        tree_frame = ttk.Frame(self)
        tree_frame.pack(fill="both", expand=True, pady=8)

        self.table = common.CheckTable(tree_frame, columns, stretch_col=3, height=14)
        self.table.tree.pack(side="left", fill="both", expand=True)
        ysb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.table.tree.yview)
        ysb.pack(side="right", fill="y")
        self.table.tree.configure(yscrollcommand=ysb.set)

        # ИСПРАВЛЕНО: Привязываем клики по чекбоксам к динамическому обновлению счетчика освобождаемого места
        self.table.tree.bind("<ButtonRelease-1>", lambda event: self.after(50, self._update_total))

        # --- Нижняя панель: статистика и кнопки ---
        info = ttk.Frame(self)
        info.pack(fill="x", pady=(0, 4))
        self.total_var = tk.StringVar(value="Нажмите «Сканировать».")
        ttk.Label(info, textvariable=self.total_var).pack(side="left")

        self.btn_cancel = ttk.Button(info, text="Отмена", command=self.cancel_scan,
                                     state="disabled")
        self.btn_cancel.pack(side="right", padx=2)
        self.btn_clean = ttk.Button(info, text="Очистить выбранное", command=self.clean_selected,
                                    state="disabled")
        self.btn_clean.pack(side="right", padx=2)
        self.btn_scan = ttk.Button(info, text="Сканировать", command=self.scan)
        self.btn_scan.pack(side="right", padx=2)

        # --- Опции ---
        opt = ttk.Frame(self)
        opt.pack(fill="x")
        self.permanent_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt, text="Удалять безвозвратно (минуя корзину) — не рекомендуется",
                        variable=self.permanent_var).pack(side="left")
        self.select_all_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt, text="Выбрать всё", variable=self.select_all_var,
                        command=self._on_select_all).pack(side="right")

        self.status_var = tk.StringVar(
            value="Сканирование покажет, сколько места занимает каждая категория.")
        ttk.Label(self, textvariable=self.status_var, foreground="#666666",
                  wraplength=900, justify="left").pack(anchor="w", pady=(6, 0))

    # ------------------------------------------------------------------
    def _on_select_all(self):
        if self.report:
            self.table.toggle_all(self.select_all_var.get())
            self._update_total()

    # ------------------------------------------------------------------
    def scan(self):
        if self.busy:
            return
        self._set_busy(True)
        self.table.clear()
        self.report = []
        self.total_var.set("Анализ пространства…")

        def work(cancel, q):
            def progress(i, n, name):
                q.put(("progress", f"Анализ {i}/{n}: {name}"))
            return self.cleaner.analyze(progress_cb=progress)

        self.cancel = common.run_task(self, work, self._on_scan_msg)

    def _on_scan_msg(self, kind, payload, _cancel):
        if kind == "progress":
            self.status_var.set(payload)
        elif kind == "done":
            self.report = payload
            self._fill_report()
        elif kind == "error":
            common.show_error("Ошибка сканирования", payload)
            self._finish()

    def _fill_report(self):
        self.table.clear()
        items = []
        for rec in self.report:
            if rec["exists"]:
                size_txt = utils.human_size(rec["size"])
                desc = rec["roots"] or (rec["note"] or "—")
            else:
                size_txt = "—"
                desc = "не обнаружено"
            items.append((rec["key"], rec["title"], size_txt, desc))
        self.table.set_items(items, checked=True)
        self._update_total()
        self.select_all_var.set(True)
        self._finish()
        if not items:
            self.status_var.set("Ни одной категории мусора не найдено.")
        else:
            self.status_var.set("Анализ завершён. Выберите категории и нажмите «Очистить выбранное».")

    def _update_total(self):
        try:
            if not self.report:
                return
            total = sum(r["size"] for r in self.report
                        if r["exists"] and self.table.is_checked(r["key"]))
            n = self.table.count_checked()
            self.total_var.set(f"Выбрано категорий: {n} · освободится ≈ {utils.human_size(total)}")
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    def clean_selected(self):
        if self.busy:
            return
        keys = self.table.checked_items()
        if not keys:
            common.show_info("Ничего не выбрано", "Отметьте категории для очистки.")
            return
        total = sum(r["size"] for r in self.report if r["key"] in keys)
        msg = (f"Выбрано категорий: {len(keys)}.\n"
               f"Освободится примерно {utils.human_size(total)}.\n\n"
               "Выполнить очистку?")
        if not common.confirm(self, "Подтверждение", msg):
            return
        to_recycle = not self.permanent_var.get()

        self._set_busy(True)
        self.status_var.set("Очистка…")

        def work(cancel, q):
            def progress(i, n, name):
                q.put(("progress", f"Очистка {i}/{n}: {name}"))
            return self.cleaner.clean(keys, to_recycle=to_recycle,
                                      progress_cb=progress, cancel=cancel)

        self.cancel = common.run_task(self, work, self._on_clean_msg)

    def _on_clean_msg(self, kind, payload, _cancel):
        if kind == "progress":
            self.status_var.set(payload)
        elif kind == "done":
            res = payload
            errs = res["errors"]
            freed = utils.human_size(res["freed"])
            if errs:
                shown = "\n".join(errs[:8])
                common.show_error(
                    "Очистка завершена с ошибками",
                    f"Освобождено: {freed}\nОбъектов удалено: {res['removed_files']}\n\n"
                    f"Не удалось (возможно, занято системой или нужны права):\n{shown}")
            else:
                common.show_info("Готово",
                                 f"Освобождено: {freed}\n"
                                 f"Удалено объектов: {res['removed_files']}")
            
            # ИСПРАВЛЕНО: Безопасное завершение операции без автозапуска рекурсивного сканирования диска.
            # Теперь состояние очищается корректно, мотивируя сделать повторный анализ вручную.
            self.report = []
            self.table.clear()
            self.total_var.set("Очистка завершена.")
            self.status_var.set("Готово. Нажмите «Сканировать» для повторного анализа системы.")
            self._finish()
        elif kind == "error":
            common.show_error("Ошибка очистки", payload)
            self._finish()

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
        state_off = "disabled" if busy else "normal"
        self.btn_scan.config(state=state_off)
        self.btn_clean.config(state=state_off)
        if not busy and self.report:
            self.btn_clean.config(state="normal")
        self.btn_cancel.config(state="normal" if busy else "disabled")
