"""Вкладка «Автозагрузка» — программы, стартующие вместе с Windows."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import customtkinter

from cleaners import startup
from ui import common


class StartupTab(customtkinter.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.cancel: common.TaskHandle | None = None
        self.busy = False
        self.entries: list[startup.StartupEntry] = []
        self._rows: dict[str, startup.StartupEntry] = {}
        self._build()
        self.after(200, self.refresh)

    def _build(self):
        head = customtkinter.CTkFrame(
            self, corner_radius=8, border_width=1, border_color="#333333"
        )
        head.pack(fill="x", padx=6, pady=(4, 6))
        customtkinter.CTkLabel(
            head,
            text="Программы, запускающиеся вместе с Windows:\n"
            "• Ключи реестра Run/RunOnce (HKCU, HKLM, x86/x64).\n"
            "• Папки «Автозагрузка» (пользователь + все пользователи).\n"
            "Отключение обратимо — запись переносится в скрытый раздел.",
            font=("Segoe UI", 10),
            justify="left",
        ).pack(anchor="w", padx=10, pady=8)

        btns = customtkinter.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=6, pady=4)

        self.btn_refresh = customtkinter.CTkButton(
            btns,
            text="🔄 Обновить",
            width=110,
            height=30,
            corner_radius=6,
            fg_color="#1e1e1e",
            hover_color="#333333",
            command=self.refresh,
        )
        self.btn_refresh.pack(side="left")

        self.btn_toggle = customtkinter.CTkButton(
            btns,
            text="🚫 Отключить выбранное",
            font=("Segoe UI", 11, "bold"),
            text_color="#000000",
            fg_color="#00ff66",
            hover_color="#00cc55",
            corner_radius=6,
            height=30,
            width=200,
            state="disabled",
            command=self.toggle_selected,
        )
        self.btn_toggle.pack(side="left", padx=6)

        self.btn_open = customtkinter.CTkButton(
            btns,
            text="📂 Открыть папку",
            width=140,
            height=30,
            corner_radius=6,
            fg_color="#1e1e1e",
            hover_color="#333333",
            state="disabled",
            command=self.open_selected,
        )
        self.btn_open.pack(side="left", padx=2)

        mid = customtkinter.CTkFrame(
            self, corner_radius=8, border_width=1, border_color="#333333"
        )
        mid.pack(fill="both", expand=True, padx=6, pady=4)

        cols = [
            ("check", "", 36),
            ("status", "Статус", 90),
            ("name", "Название", 260),
            ("source", "Источник", 220),
            ("file", "Файл", 380),
        ]
        tf = customtkinter.CTkFrame(mid, fg_color="transparent")
        tf.pack(fill="both", expand=True, padx=6, pady=6)

        self.table = common.CheckTable(
            tf, cols, stretch_col=4, height=18, sortable=True
        )
        self.table.tree.pack(side="left", fill="both", expand=True)
        ysb = ttk.Scrollbar(tf, orient="vertical", command=self.table.tree.yview)
        ysb.pack(side="right", fill="y")
        self.table.tree.configure(yscrollcommand=ysb.set)
        self.table.tree.bind("<<TreeviewSelect>>", lambda e: self._on_select(), add="+")

        self.status_var = tk.StringVar(
            value="Нажмите «Обновить» для чтения списка автозагрузки."
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

    def refresh(self):
        if self.busy:
            return
        self._set_busy(True)
        self.table.clear()
        self._rows.clear()
        self.status_var.set("Чтение автозагрузки…")

        def work(cancel, q):
            def progress(msg):
                q.put(("progress", msg))

            return startup.scan_startup(cancel=cancel, progress_cb=progress)

        self.cancel = common.run_task(self, work, self._on_msg)

    def _on_msg(self, kind, payload, _h):
        if kind == "progress":
            self.status_var.set(payload)
        elif kind == "done":
            self.entries = payload or []
            self._fill()
            self._set_busy(False)
        elif kind == "error":
            common.show_error("Ошибка", payload)
            self._set_busy(False)

    def _fill(self):
        self.table.clear()
        self._rows.clear()
        for e in self.entries:
            iid = e.uid
            status_label = "✅ Включено" if e.enabled else "🚫 Отключено"
            if not e.exists:
                status_label += " (нет файла)"
            self.table.add_item(
                (iid, status_label, e.name, e.source_label, e.file_path),
                checked=False,
                kind="registry" if e.kind == "registry" else "folder",
                sort_keys={
                    "status": status_label,
                    "name": e.name.lower(),
                    "source": e.source_label.lower(),
                    "file": e.file_path.lower(),
                },
            )
            self._rows[iid] = e
        self.table.commit()
        enabled = sum(1 for e in self.entries if e.enabled)
        missing = sum(1 for e in self.entries if not e.exists)
        self.status_var.set(
            f"Всего записей: {len(self.entries)} · "
            f"включено: {enabled} · мёртвых (нет файла): {missing}"
        )

    def _on_select(self):
        sel = self.table.tree.selection()
        self.btn_toggle.configure(state="normal" if sel else "disabled")
        self.btn_open.configure(state="normal" if sel else "disabled")

    def toggle_selected(self):
        sel = self.table.tree.selection()
        if not sel:
            return
        entry = self._rows.get(sel[0])
        if entry is None:
            return
        target_enable = not entry.enabled
        ok, msg = startup.set_enabled(entry, target_enable)
        if not ok:
            common.show_error("Не удалось изменить", msg)
            return
        self.refresh()

    def open_selected(self):
        sel = self.table.tree.selection()
        if not sel:
            return
        entry = self._rows.get(sel[0])
        if entry is None:
            return
        if not startup.open_file_location(entry):
            common.show_info("Нет файла", "Файл не найден или путь недоступен.")

    def cancel_scan(self):
        if self.cancel is not None:
            self.cancel.stop()

    def _set_busy(self, busy: bool):
        self.busy = busy
        self.btn_refresh.configure(state="disabled" if busy else "normal")
        if not busy and not self.entries:
            self.btn_toggle.configure(state="disabled")
            self.btn_open.configure(state="disabled")
