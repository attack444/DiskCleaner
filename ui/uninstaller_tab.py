"""Графическая вкладка «Умный Деинсталлятор Приложений» на базе CustomTkinter."""

from __future__ import annotations

import os
import subprocess
import tkinter as tk
from tkinter import ttk

import customtkinter

from cleaners.safety import build_preview
from cleaners.uninstaller import (
    create_registry_backup,
    find_app_leftovers,
    get_installed_apps,
)
from cleaners.utils import move_to_recycle
from ui import common


class UninstallerTab(customtkinter.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.apps_list: list = []
        self._all_rows: list[tuple] = []  # (iid, values, sort_keys)
        self._filtered: list[tuple] = []
        self._sort_col: str | None = None
        self._sort_dir = "asc"
        self.task_handle: common.TaskHandle | None = None
        self.busy = False
        self._uninstall_proc: subprocess.Popen | None = None
        self._pending_app = None
        self._build()
        self.after(200, self.refresh_list)

    # =====================================================================
    # UI
    # =====================================================================

    def _build(self):
        toolbar = customtkinter.CTkFrame(
            self, height=45, corner_radius=6, border_width=1, border_color="#333333"
        )
        toolbar.pack(fill="x", padx=6, pady=(4, 8))

        self.btn_refresh = customtkinter.CTkButton(
            toolbar,
            text="🔄 Обновить",
            width=110,
            height=28,
            corner_radius=6,
            command=self.refresh_list,
        )
        self.btn_refresh.pack(side="left", padx=10, pady=8)

        self.status_lbl = customtkinter.CTkLabel(
            toolbar,
            text="Загрузка списка установленных программ Windows...",
            font=("Segoe UI", 10),
            text_color="#aaaaaa",
        )
        self.status_lbl.pack(side="left", padx=5)

        self.btn_uninstall = customtkinter.CTkButton(
            toolbar,
            text="📦 Удалить без остатка",
            font=("Segoe UI", 11, "bold"),
            text_color="#000000",
            fg_color="#00ff66",
            hover_color="#00cc55",
            corner_radius=6,
            width=170,
            height=28,
            state="disabled",
            command=self.start_force_uninstall,
        )
        self.btn_uninstall.pack(side="right", padx=10, pady=8)

        # Поиск
        search_row = customtkinter.CTkFrame(self, fg_color="transparent")
        search_row.pack(fill="x", padx=6, pady=(0, 4))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._apply_filter())
        customtkinter.CTkEntry(
            search_row,
            textvariable=self.search_var,
            placeholder_text="🔎 Поиск по названию, издателю, пути…",
            height=28,
            corner_radius=6,
            border_color="#333333",
        ).pack(side="left", fill="x", expand=True, padx=(0, 4))
        customtkinter.CTkButton(
            search_row,
            text="✕",
            width=32,
            height=28,
            corner_radius=6,
            fg_color="#1e1e1e",
            hover_color="#333333",
            command=lambda: self.search_var.set(""),
        ).pack(side="left", padx=(0, 6))
        self.lbl_count = customtkinter.CTkLabel(
            search_row, text="", font=("Segoe UI", 10), text_color="#888888"
        )
        self.lbl_count.pack(side="right")

        # Таблица
        mid = customtkinter.CTkFrame(
            self, corner_radius=8, border_width=1, border_color="#333333"
        )
        mid.pack(fill="both", expand=True, padx=6, pady=(0, 6))

        cols = [
            ("name", "Название программы", 380),
            ("publisher", "Издатель", 180),
            ("version", "Версия", 100),
            ("location", "Путь установки", 350),
        ]
        tf = customtkinter.CTkFrame(mid, fg_color="transparent")
        tf.pack(fill="both", expand=True, padx=6, pady=6)
        self.tree = ttk.Treeview(
            tf,
            columns=[c[0] for c in cols],
            show="headings",
            height=22,
            selectmode="browse",
        )
        self.tree.pack(side="left", fill="both", expand=True)
        for key, text, width in cols:
            self.tree.heading(
                key, text=text, anchor="w", command=lambda k=key: self._on_sort(k)
            )
            self.tree.column(key, width=width, anchor="w")

        ysb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        ysb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=ysb.set)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Button-3>", self._on_right_click)

        # ПКМ
        self._menu = tk.Menu(
            self,
            tearoff=0,
            bg="#1e1e1e",
            fg="#ffffff",
            activebackground="#333333",
            activeforeground="#00ff66",
        )
        self._menu.add_command(
            label="📂 Открыть папку установки", command=self._open_install_dir
        )
        self._menu.add_command(
            label="📋 Скопировать команду удаления", command=self._copy_uninstall_cmd
        )

    # =====================================================================
    # Список
    # =====================================================================

    def refresh_list(self):
        self.tree.delete(*self.tree.get_children())
        self._all_rows.clear()
        self._filtered.clear()
        self.status_lbl.configure(text="Сканирование реестра…")
        self.apps_list = get_installed_apps()
        for i, app in enumerate(self.apps_list):
            iid = f"app_{i}"
            values = (
                app.display_name,
                app.publisher,
                app.version,
                app.install_location,
            )
            self._all_rows.append(
                (
                    iid,
                    values,
                    {
                        "name": (app.display_name or "").lower(),
                        "publisher": (app.publisher or "").lower(),
                        "version": (app.version or "").lower(),
                        "location": (app.install_location or "").lower(),
                    },
                )
            )
        self._apply_filter()

    def _apply_filter(self):
        text = (self.search_var.get() or "").strip().lower()
        if text:
            self._filtered = [
                (iid, vals, sk)
                for (iid, vals, sk) in self._all_rows
                if text in " ".join(str(v) for v in vals).lower()
            ]
        else:
            self._filtered = list(self._all_rows)

        if self._sort_col:
            col = self._sort_col
            reverse = self._sort_dir == "desc"
            self._filtered.sort(key=lambda r: r[2].get(col, ""), reverse=reverse)

        self.tree.delete(*self.tree.get_children())
        for iid, vals, _sk in self._filtered:
            self.tree.insert("", "end", iid=iid, values=vals)

        self.lbl_count.configure(
            text=f"Показано: {len(self._filtered)} из {len(self._all_rows)}"
        )
        self.status_lbl.configure(
            text=f"Всего установлено программ: {len(self._all_rows)}"
        )

    def _on_sort(self, key: str):
        if self._sort_col == key:
            self._sort_dir = "desc" if self._sort_dir == "asc" else "asc"
        else:
            self._sort_col = key
            self._sort_dir = "asc"
        titles = {
            "name": "Название программы",
            "publisher": "Издатель",
            "version": "Версия",
            "location": "Путь установки",
        }
        for k, t in titles.items():
            arrow = ""
            if k == self._sort_col:
                arrow = " ▲" if self._sort_dir == "asc" else " ▼"
            self.tree.heading(k, text=t + arrow)
        self._apply_filter()

    def _on_select(self, _e=None):
        self.btn_uninstall.configure(
            state="normal" if self.tree.selection() and not self.busy else "disabled"
        )

    # =====================================================================
    # ПКМ
    # =====================================================================

    def _on_right_click(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        self.tree.selection_set(iid)
        try:
            self._menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._menu.grab_release()

    def _selected_app(self):
        sel = self.tree.selection()
        if not sel:
            return None
        iid = sel[0]
        try:
            idx = int(iid.split("_")[-1])
        except ValueError:
            return None
        if 0 <= idx < len(self.apps_list):
            return self.apps_list[idx]
        return None

    def _open_install_dir(self):
        app = self._selected_app()
        if not app:
            return
        path = app.install_location
        if path and os.path.isdir(path):
            try:
                os.startfile(path)
                return
            except Exception:
                pass
        # fallback — открыть по exe
        if app.uninstall_string:
            exe = app.uninstall_string.strip().strip('"').split('"')[0]
            d = os.path.dirname(exe)
            if os.path.isdir(d):
                try:
                    os.startfile(d)
                    return
                except Exception:
                    pass
        common.show_info("Нет папки", "Не удалось определить папку установки.")

    def _copy_uninstall_cmd(self):
        app = self._selected_app()
        if not app:
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(app.uninstall_string)
            common.show_info(
                "Скопировано", "Команда удаления скопирована в буфер обмена."
            )
        except Exception as e:
            common.show_error("Ошибка", f"Не удалось скопировать: {e}")

    # =====================================================================
    # Запуск деинсталляции
    # =====================================================================

    def start_force_uninstall(self):
        app = self._selected_app()
        if not app or self.busy:
            return

        msg = (
            f"Деинсталлировать «{app.display_name}»?\n\n"
            f"• Будет создан бэкап ветки реестра\n"
            f"• Запустится штатный деинсталлятор программы\n"
            f"• После завершения будет предложено удалить остатки"
        )
        if not common.confirm(self, "Деинсталляция софта", msg):
            return

        self._set_busy(True)

        backup_dir = os.path.join(
            os.environ.get("TEMP", "C:\\Temp"), "DiskCleaner_Backups"
        )
        try:
            backup_file = create_registry_backup(app, backup_dir)
        except Exception as e:
            backup_file = None
            self.status_lbl.configure(text=f"Бэкап не удался: {e}")

        if backup_file:
            self.status_lbl.configure(
                text=f"Бэкап: {os.path.basename(backup_file)}. Запуск деинсталлятора…"
            )
        else:
            self.status_lbl.configure(text="Запуск штатного деинсталлятора…")

        try:
            self._uninstall_proc = subprocess.Popen(
                app.uninstall_string,
                shell=True,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
        except Exception as e:
            common.show_error(
                "Ошибка запуска", f"Не удалось запустить деинсталлятор: {e}"
            )
            self._set_busy(False)
            return

        self._pending_app = app
        self.after(700, self._poll_uninstall)

    def _poll_uninstall(self):
        proc = self._uninstall_proc
        if proc is None:
            return
        if proc.poll() is None:
            self.after(700, self._poll_uninstall)
            return

        app = self._pending_app
        self._uninstall_proc = None
        self._pending_app = None
        self.status_lbl.configure(text="Поиск остатков после удаления…")

        def work(cancel, q):
            return find_app_leftovers(app)

        self.task_handle = common.run_task(self, work, self._on_leftovers_ready)

    def _on_leftovers_ready(self, kind, payload, _handle):
        if kind == "error":
            common.show_error("Ошибка поиска остатков", payload)
            self._set_busy(False)
            self.refresh_list()
            return

        leftovers = payload or []
        if not leftovers:
            common.show_info(
                "Готово", "Штатное удаление завершено. Остатков не найдено."
            )
            self._set_busy(False)
            self.refresh_list()
            return

        items = [{"path": p, "kind": "dir", "size": 0} for p in leftovers]
        preview = build_preview(items)
        dlg = common.PreviewDialog(self, preview, title="Остатки после удаления")
        self.wait_window(dlg)

        if not dlg.result:
            self._set_busy(False)
            self.refresh_list()
            return

        bad = move_to_recycle(leftovers)
        removed = len(leftovers) - len(bad)
        msg = f"Удалено остаточных папок: {removed} из {len(leftovers)}."
        if bad:
            msg += f"\n\nЗаблокировано: {len(bad)}."
        common.show_info("Готово", msg)
        self._set_busy(False)
        self.refresh_list()

    # =====================================================================
    # Служебное
    # =====================================================================

    def _set_busy(self, busy: bool):
        self.busy = busy
        self.btn_refresh.configure(state="disabled" if busy else "normal")
        self.btn_uninstall.configure(
            state="disabled" if busy or not self.tree.selection() else "normal"
        )
