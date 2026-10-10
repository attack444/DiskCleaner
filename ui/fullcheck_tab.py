"""Вкладка «⚡ Полная проверка»: один клик — все сканеры + удаление найденного."""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk

from cleaners import run_full_check, SystemCleaner, utils
from ui import common


class FullCheckTab(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=8)
        self.cancel = None
        self.busy = False
        self.result: list[dict] = []
        self.rows: list[dict] = []       
        self.junk_keys: list[str] = []
        self._build()

    # ------------------------------------------------------------------
    def _build(self):
        header = ttk.Label(
            self,
            text="Одна кнопка запускает все проверки сразу:\n"
                 "• системный мусор и корзина   • дубликаты файлов\n"
                 "• большие файлы (>100 МБ)     • пустые папки.\n"
                 "Найденное появится списком — отметьте и удалите одной кнопкой.",
            justify="left")
        header.pack(anchor="w")

        self.picker = common.FolderPicker(self, label="Где искать (по умолчанию — домашняя):",
                                          initial=os.path.expanduser("~"), width=55)
        self.picker.pack(fill="x", pady=4)

        # Таблица результатов
        mid = ttk.LabelFrame(self, text="Найденное (отметьте для удаления)", padding=8)
        mid.pack(fill="both", expand=True, pady=6)
        cols = [("check", "", 36), ("type", "Тип", 90),
                ("path", "Объект", 520), ("size", "Размер", 90)]
        tf = ttk.Frame(mid)
        tf.pack(fill="both", expand=True)
        self.table = common.CheckTable(tf, cols, stretch_col=2, height=18)
        self.table.tree.pack(side="left", fill="both", expand=True)
        ysb = ttk.Scrollbar(tf, orient="vertical", command=self.table.tree.yview)
        ysb.pack(side="right", fill="y")
        self.table.tree.configure(yscrollcommand=ysb.set)

        # ИСПРАВЛЕНО: Динамический пересчет веса выбранных папок/файлов при кликах по таблице
        self.table.tree.bind("<ButtonRelease-1>", lambda event: self.after(50, self._update_sel_info))

        # Нижняя панель
        self.status_var = tk.StringVar(
            value="Нажмите «Полная проверка» — сканеры запустятся все сразу.")
        ttk.Label(self, textvariable=self.status_var, foreground="#666666",
                  wraplength=900, justify="left").pack(anchor="w", pady=(4, 0))

        row = ttk.Frame(self)
        row.pack(fill="x", pady=6)
        self.lbl_sum = tk.StringVar(value="")
        ttk.Label(row, textvariable=self.lbl_sum).pack(side="left")

        self.btn_cancel = ttk.Button(row, text="Отмена", command=self.cancel_scan,
                                     state="disabled")
        self.btn_cancel.pack(side="right", padx=2)
        self.btn_delete = ttk.Button(row, text="Удалить выбранное", command=self.delete_selected,
                                     state="disabled")
        self.btn_delete.pack(side="right", padx=2)
        self.btn_run = ttk.Button(row, text="⚡ Полная проверка", command=self.run_check)
        self.btn_run.pack(side="right", padx=2)

        ttk.Button(self, text="Показать в отдельных вкладках →",
                   command=self.open_tabs_hint).pack(anchor="e")

    def open_tabs_hint(self):
        common.show_info(
            "Полная проверка",
            "Результаты можно посмотреть подробнее и точечно удалить "
            "на отдельных вкладках: «Очистка системы», «Дубликаты», "
            "«Большие файлы», «Пустые папки».")

    # ------------------------------------------------------------------
    def run_check(self):
        if self.busy:
            return
        root = self.picker.get()
        if not root:
            root = os.path.expanduser("~")

        self._set_busy(True)
        self.table.clear()
        self.result = []
        self.rows = []
        self.junk_keys = []
        self.status_var.set("Запуск полной проверки… (может занять время)")

        def work(cancel, q):
            return run_full_check(cancel, q, root=root)

        self.cancel = common.run_task(self, work, self._on_msg)

    def _on_msg(self, kind, payload, _cancel):
        if kind == "progress":
            self.status_var.set(payload)
        elif kind == "done":
            data = payload
            if data.get("cancelled"):
                self.status_var.set("Проверка отменена.")
                self._finish()
                return
            self.result = data["result"]
            self._fill(data.get("summary", {}))
        elif kind == "error":
            common.show_error("Ошибка проверки", payload)
            self._finish()

    def _fill(self, summary: dict):
        self.table.clear()
        self.rows = []
        kind_labels = {
            "junk": "🧹 Мусор",
            "duplicate": "🔁 Дубликат",
            "big": "🗜 Большой",
            "empty": "📂 Пустая",
        }
        idx = 0
        for item in self.result:
            iid = f"row{idx}"
            idx += 1
            checked = item.get("auto", True)
            label = kind_labels.get(item["kind"], item["kind"])
            self.table.add_item((iid, label, item["path"],
                                 utils.human_size(item["size"])), checked=checked)
            self.rows.append({"iid": iid, "path": item["path"],
                              "size": item["size"], "kind": item["kind"],
                              "meta": item.get("meta", {})})
            if item["kind"] == "junk":
                key = item["meta"].get("key")
                if key:
                    self.junk_keys.append(key)

        s = summary
        total_size = s.get("total_size", 0)
        self.lbl_sum.set(
            f"Найдено объектов: {len(self.result)} · можно освободить ≈ "
            f"{utils.human_size(total_size)}\n"
            f"(мусор: {utils.human_size(s.get('junk_size', 0))}, "
            f"дубликаты: {utils.human_size(s.get('dup_size', 0))}, "
            f"большие: {utils.human_size(s.get('big_size', 0))})")
        self._finish()
        if not self.result:
            self.status_var.set("Всё чисто — ничего не найдено.")
            return
        self.status_var.set(
            "Проверка завершена. Снимите галочки с файлов, которые нужно сохранить, "
            "и нажмите «Удалить выбранное».")
        self.btn_delete.config(state="normal")

    # ------------------------------------------------------------------
    def delete_selected(self):
        if self.busy:
            return
        checked = self.table.checked_items()
        if not checked:
            common.show_info("Ничего не выбрано", "Отметьте объекты для удаления.")
            return

        to_recycle_paths = []
        to_recycle_dirs = []
        junk_keys = []
        
        # ИСПРАВЛЕНО: Кэшируем размеры файлов в памяти ДО удаления, предотвращая обнуление freed-байтов
        static_sizes = {}

        for row in self.rows:
            if row["iid"] not in checked:
                continue
            kind = row["kind"]
            static_sizes[row["path"]] = row["size"]
            
            if kind == "junk":
                key = row["meta"].get("key")
                if key:
                    junk_keys.append(key)
            elif kind in ("duplicate", "big"):
                to_recycle_paths.append(row["path"])
            elif kind == "empty":
                to_recycle_dirs.append(row["path"])

        size_sum = sum(r["size"] for r in self.rows if r["iid"] in checked)
        msg = (f"Выбрано объектов: {len(checked)}.\n"
               f"Будет освобождено примерно: {utils.human_size(size_sum)}\n\n"
               "Категории мусора будут очищены, файлы — перемещены в корзину.\n"
               "Продолжить?")
        if not common.confirm(self, "Удаление выбранного", msg):
            return

        self._set_busy(True)
        self.status_var.set("Удаление…")
        self.btn_delete.config(state="disabled")

        def work(cancel, q):
            freed = 0
            removed = 0
            errors: list[str] = []
            
            if junk_keys:
                cleaner = SystemCleaner()
                res = cleaner.clean(junk_keys, to_recycle=True,
                                    progress_cb=None, cancel=cancel)
                freed += res["freed"]
                removed += res["removed_files"]
                errors.extend(res["errors"])
                
            if to_recycle_paths:
                bad = utils.move_to_recycle(to_recycle_paths)
                ok = [p for p in to_recycle_paths if p not in bad]
                removed += len(ok)
                # ИСПРАВЛЕНО: Берём точный размер из RAM-кэша static_sizes вместо удаленного дискового файла
                freed += sum(static_sizes.get(p, 0) for p in ok)
                errors.extend(f"Файл: {os.path.basename(b)}" for b in bad)
                
            if to_recycle_dirs:
                bad = utils.move_to_recycle(to_recycle_dirs)
                ok = [p for p in to_recycle_dirs if p not in bad]
                removed += len(ok)
                errors.extend(f"Папка: {os.path.basename(b)}" for b in bad)
            return {"freed": freed, "removed": removed, "errors": errors}

        self.cancel = common.run_task(self, work, self._on_delete_msg)

       # === Окончание класса FullCheckTab в файле ui/fullcheck_tab.py ===

    def _on_delete_msg(self, kind, payload, _cancel):
        """Обрабатывает сообщения от фонового потока удаления объектов."""
        if kind == "done":
            res = payload
            if res["errors"]:
                common.show_error("Удаление с ошибками",
                                  f"Освобождено: {utils.human_size(res['freed'])}\n"
                                  f"Не удалось:\n" + "\n".join(res["errors"][:8]))
            else:
                common.show_info("Готово",
                                 f"Освобождено: {utils.human_size(res['freed'])}\n"
                                 f"Удалено объектов: {res['removed']}")
            self._finish()
            
            # ИСПРАВЛЕНО: Строки возвращены на уровень 12 пробелов (внутрь блока done)
            self.lbl_sum.set("Повторите полную проверку для обновления результатов.")
            self.status_var.set("Готово. Нажмите «Полная проверка», чтобы увидеть актуальное состояние.")
            self.result = []
            self.rows = []
            self.table.clear()
            
        elif kind == "error":
            common.show_error("Ошибка", payload)
            self._finish()

    def _update_sel_info(self):
        """Динамически пересчитывает объем выбранного мусора при кликах."""
        try:
            checked = self.table.checked_items()
            size_sum = sum(r["size"] for r in self.rows if r["iid"] in checked)
            self.status_var.set(f"Выбрано для удаления объектов: {len(checked)} · Общий вес: {utils.human_size(size_sum)}")
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    def cancel_scan(self):
        """Отменяет асинхронную операцию полной проверки всех сканеров."""
        if self.cancel is not None and not self.cancel.is_set():
            self.cancel.set()
            self.status_var.set("Отмена…")
            self.btn_cancel.config(state="disabled")

    def _finish(self):
        """Сбрасывает токен занятости интерфейса."""
        self._set_busy(False)
        self.cancel = None

    def _set_busy(self, busy: bool):
        """Управляет доступностью кнопок управления во время активных задач."""
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.btn_run.config(state=state)
        self.btn_cancel.config(state="normal" if busy else "disabled")
        if not busy and self.result:
            self.btn_delete.config(state="normal")
