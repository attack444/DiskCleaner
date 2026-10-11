"""Вкладка «Чистка реестра» — битые ссылки в MuiCache и App Paths."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import customtkinter

from cleaners.registry_cleaner import (
    delete_registry_params,
    scan_invalid_mui_cache,
)
from ui import common


class RegistryTab(customtkinter.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.cancel: common.TaskHandle | None = None
        self.busy = False
        self.entries: list[dict] = []
        self._rows: list[dict] = []
        self._build()

    def _build(self):
        head = customtkinter.CTkFrame(
            self, corner_radius=8, border_width=1, border_color="#333333"
        )
        head.pack(fill="x", padx=6, pady=(4, 6))
        customtkinter.CTkLabel(
            head,
            text="Сканирование устаревших записей реестра Windows:\n"
            "• MuiCache — кэш дружественных имён запущенных программ.\n"
            "• App Paths — «быстрый запуск» программ, которых уже нет.\n"
            "Все изменения обратимы через .reg-бэкап (создаётся автоматически).",
            font=("Segoe UI", 10),
            justify="left",
        ).pack(anchor="w", padx=10, pady=8)

        btns = customtkinter.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=6, pady=4)

        self.btn_scan = customtkinter.CTkButton(
            btns,
            text="🔍 Найти устаревшие записи",
            font=("Segoe UI", 11, "bold"),
            text_color="#000000",
            fg_color="#00ff66",
            hover_color="#00cc55",
            corner_radius=6,
            height=30,
            width=220,
            command=self.run_scan,
        )
        self.btn_scan.pack(side="left")

        self.btn_delete = customtkinter.CTkButton(
            btns,
            text="🗑 Удалить выбранное",
            font=("Segoe UI", 11, "bold"),
            fg_color="#1e1e1e",
            hover_color="#333333",
            corner_radius=6,
            height=30,
            width=170,
            state="disabled",
            command=self.delete_selected,
        )
        self.btn_delete.pack(side="left", padx=6)

        self.btn_cancel = customtkinter.CTkButton(
            btns,
            text="✕",
            width=40,
            height=30,
            corner_radius=6,
            fg_color="#1e1e1e",
            hover_color="#333333",
            state="disabled",
            command=self.cancel_scan,
        )
        self.btn_cancel.pack(side="right")

        mid = customtkinter.CTkFrame(
            self, corner_radius=8, border_width=1, border_color="#333333"
        )
        mid.pack(fill="both", expand=True, padx=6, pady=4)

        customtkinter.CTkLabel(
            mid,
            text="Найденные записи (отметьте для удаления)",
            font=("Segoe UI", 11, "bold"),
            text_color="#00ff66",
        ).pack(anchor="w", padx=10, pady=(6, 2))

        cols = [
            ("check", "", 36),
            ("hive", "Куст", 70),
            ("name", "Запись", 260),
            ("path", "Мёртвый путь", 460),
        ]
        tf = customtkinter.CTkFrame(mid, fg_color="transparent")
        tf.pack(fill="both", expand=True, padx=6, pady=(0, 6))

        self.table = common.CheckTable(
            tf, cols, stretch_col=3, height=18, sortable=True
        )
        self.table.tree.pack(side="left", fill="both", expand=True)
        ysb = ttk.Scrollbar(tf, orient="vertical", command=self.table.tree.yview)
        ysb.pack(side="right", fill="y")
        self.table.tree.configure(yscrollcommand=ysb.set)

        self.status_var = tk.StringVar(
            value="Нажмите «Найти устаревшие записи» — "
            "приложение просканирует HKCU и HKLM."
        )
        customtkinter.CTkLabel(
            self,
            textvariable=self.status_var,
            font=("Segoe UI", 10),
            text_color="#888888",
            wraplength=1000,
            justify="left",
        ).pack(anchor="w", padx=10, pady=(4, 6))

    # ------------------------------------------------------------------

    def run_scan(self):
        if self.busy:
            return
        self._set_busy(True)
        self.table.clear()
        self.entries = []
        self._rows = []
        self.status_var.set("Сканирование реестра…")

        def work(cancel, q):
            def progress(msg):
                q.put(("progress", msg))

            return scan_invalid_mui_cache(cancel=cancel, progress_cb=progress)

        self.cancel = common.run_task(self, work, self._on_msg)

    def _on_msg(self, kind, payload, _handle):
        if kind == "progress":
            self.status_var.set(payload)
        elif kind == "done":
            self.entries = payload or []
            self._fill()
            self._set_busy(False)
        elif kind == "error":
            common.show_error("Ошибка сканирования", payload)
            self._set_busy(False)

    def _fill(self):
        self.table.clear()
        self._rows = []
        for i, e in enumerate(self.entries):
            iid = f"r{i}"
            self.table.add_item(
                (
                    iid,
                    e.get("hive", "?"),
                    e.get("display_name", e.get("param_name", "")),
                    e.get("dead_path", ""),
                ),
                checked=True,
                sort_keys={
                    "hive": e.get("hive", "").lower(),
                    "name": e.get("display_name", "").lower(),
                    "path": e.get("dead_path", "").lower(),
                },
            )
            self._rows.append({"iid": iid, "entry": e})

        self.table.commit()
        n = len(self.entries)
        if n == 0:
            self.status_var.set("Устаревших записей не найдено — реестр чист.")
        else:
            self.status_var.set(
                f"Найдено записей: {n}. Можно удалить выбранные — "
                f"все они ссылаются на несуществующие программы."
            )
            self.btn_delete.configure(state="normal")

    def delete_selected(self):
        if self.busy or not self.entries:
            return
        checked = set(self.table.checked_items())
        to_delete = [r["entry"] for r in self._rows if r["iid"] in checked]
        if not to_delete:
            common.show_info("Ничего не выбрано", "Отметьте записи для удаления.")
            return

        msg = (
            f"Удалить {len(to_delete)} записей из реестра?\n\n"
            f"Действие отменяемо: при следующем запуске приложения "
            f"будет предложен бэкап."
        )
        if not common.confirm(self, "Чистка реестра", msg):
            return

        self._set_busy(True)
        self.status_var.set("Удаление записей…")

        def work(cancel, q):
            return delete_registry_params(to_delete)

        def done(kind, payload, _h):
            if kind == "done":
                n = int(payload or 0)
                common.show_info("Готово", f"Удалено записей: {n}.")
                self._set_busy(False)
                self.run_scan()  # пере-сканируем, чтобы показать актуальное
            elif kind == "error":
                common.show_error("Ошибка", payload)
                self._set_busy(False)

        self.cancel = common.run_task(self, work, done)

    def cancel_scan(self):
        if self.cancel is not None:
            self.cancel.stop()
            self.status_var.set("Отмена…")
            self.btn_cancel.configure(state="disabled")

    def _set_busy(self, busy: bool):
        self.busy = busy
        self.btn_scan.configure(state="disabled" if busy else "normal")
        self.btn_cancel.configure(state="normal" if busy else "disabled")
        if not busy and not self.entries:
            self.btn_delete.configure(state="disabled")
