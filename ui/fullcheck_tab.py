from __future__ import annotations

import os
import time as _time
import tkinter as tk
from tkinter import ttk

import customtkinter

from cleaners.full_check import run_full_check
from cleaners.report import (
    default_report_path,
    open_in_browser,
    write_csv_report,
    write_html_report,
)
from cleaners.safety import DeletionEntry, DeletionLog, Whitelist, build_preview
from cleaners.system_cleaner import SystemCleaner
from cleaners.utils import human_size, move_to_recycle, request_delete_on_reboot
from ui import common

try:
    from cleaners.smart_rules import FileVerdict, judge_file
except Exception:
    FileVerdict = None

    def judge_file(path, size=0, context=None):
        class _J:
            verdict = None
            reason = ""

        return _J()


KIND_LABELS = {
    "junk": "🧹 Мусор",
    "duplicate": "🔁 Копия",
    "big": "🗜 Крупный",
    "empty": "📂 Пустая",
}
KIND_FILTERS = dict(KIND_LABELS)


class FullCheckTab(customtkinter.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.cancel: common.TaskHandle | None = None
        self.busy = False
        self.result: list[dict] = []
        self.row_index: dict[str, dict] = {}  # iid -> row
        self.whitelist = Whitelist()
        self.deletion_log: DeletionLog | None = None
        self._last_summary: dict = {}

        self.dry_run = tk.BooleanVar(value=False)
        self.show_protected = tk.BooleanVar(value=False)

        self._build()

    # =====================================================================
    # UI
    # =====================================================================

    def _build(self):
        left_panel = customtkinter.CTkFrame(self, fg_color="transparent")
        left_panel.pack(side="left", fill="y", padx=(0, 10))

        # --- Параметры
        top_opts = customtkinter.CTkFrame(
            left_panel, corner_radius=8, border_width=1, border_color="#333333"
        )
        top_opts.pack(fill="x", anchor="n", ipady=8, ipadx=8)
        customtkinter.CTkLabel(
            top_opts,
            text="Параметры анализа",
            font=("Segoe UI", 11, "bold"),
            text_color="#00ff66",
        ).pack(anchor="w", padx=10, pady=(6, 2))

        self.picker = common.FolderPicker(
            top_opts, label="Цель:", initial=os.path.expanduser("~"), width=25
        )
        self.picker.pack(fill="x", pady=4, padx=10)

        self.profile_sel = common.ProfileSelector(top_opts)
        self.profile_sel.pack(fill="x", padx=10, pady=(4, 0))

        opts_box = customtkinter.CTkFrame(top_opts, fg_color="transparent")
        opts_box.pack(fill="x", padx=10, pady=(6, 0))
        customtkinter.CTkCheckBox(
            opts_box,
            text="Пробный прогон (ничего не удалять)",
            variable=self.dry_run,
            font=("Segoe UI", 10),
            text_color="#cccccc",
            fg_color="#00ff66",
            hover_color="#00cc55",
            checkbox_width=18,
            checkbox_height=18,
        ).pack(anchor="w", pady=3)
        customtkinter.CTkCheckBox(
            opts_box,
            text="Показывать защищённые файлы",
            variable=self.show_protected,
            font=("Segoe UI", 10),
            text_color="#cccccc",
            fg_color="#00ff66",
            hover_color="#00cc55",
            checkbox_width=18,
            checkbox_height=18,
            command=self._refill_from_cache,
        ).pack(anchor="w", pady=3)

        # --- Карта памяти
        self.stats_frame = customtkinter.CTkFrame(
            left_panel, corner_radius=8, border_width=1, border_color="#333333"
        )
        self.stats_frame.pack(fill="both", expand=True, pady=(10, 0), ipadx=8)
        customtkinter.CTkLabel(
            self.stats_frame,
            text="📊 Распределение данных",
            font=("Segoe UI", 11, "bold"),
            text_color="#00ff66",
        ).pack(anchor="w", padx=10, pady=(6, 4))
        self.stat_vars: dict[str, tk.StringVar] = {
            name: tk.StringVar(value=f"{name}: 0 Б")
            for name in ("Видео", "Изображения", "Документы", "Архивы", "Другое")
        }
        for name, var in self.stat_vars.items():
            customtkinter.CTkLabel(
                self.stats_frame,
                textvariable=var,
                font=("Segoe UI", 10),
                justify="left",
            ).pack(anchor="w", padx=15, pady=4)

        # --- Правая панель
        right_panel = customtkinter.CTkFrame(self, fg_color="transparent")
        right_panel.pack(side="right", fill="both", expand=True)

        mid = customtkinter.CTkFrame(
            right_panel, corner_radius=8, border_width=1, border_color="#333333"
        )
        mid = customtkinter.CTkFrame(
            right_panel, corner_radius=8, border_width=1, border_color="#333333"
        )
        # mid упакуем ПОСЛЕ row и bottom, чтобы нижние панели всегда были видны

        head = customtkinter.CTkFrame(mid, fg_color="transparent")
        head.pack(fill="x", padx=10, pady=(6, 2))
        customtkinter.CTkLabel(
            head,
            text="Найденные объекты",
            font=("Segoe UI", 11, "bold"),
            text_color="#00ff66",
        ).pack(side="left")
        customtkinter.CTkLabel(
            head,
            text="(клик по заголовку — сортировка; ПКМ — белый список)",
            font=("Segoe UI", 9),
            text_color="#777777",
        ).pack(side="left", padx=8)

        # FilterBar
        # FilterBar — без table, привяжем после создания CheckTable
        self.filter_bar = common.FilterBar(mid, kinds=KIND_FILTERS)
        self.filter_bar.pack(fill="x", padx=10, pady=(4, 6))

        # Таблица
        cols = [
            ("check", "", 36),
            ("type", "Тип", 110),
            ("path", "Объект", 420),
            ("size", "Размер", 100),
        ]
        tf = customtkinter.CTkFrame(mid, fg_color="transparent")
        tf.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self.table = common.CheckTable(
            tf, cols, stretch_col=2, height=18, sortable=True, kind_labels=KIND_LABELS
        )
        self.table.tree.pack(side="left", fill="both", expand=True)
        ysb = ttk.Scrollbar(tf, orient="vertical", command=self.table.tree.yview)
        ysb.pack(side="right", fill="y")
        self.table.tree.configure(yscrollcommand=ysb.set)

        # Связываем FilterBar с реальной таблицей
        self.filter_bar.attach(self.table)

        # ПКМ
        self._wl_menu = tk.Menu(
            self,
            tearoff=0,
            bg="#1e1e1e",
            fg="#ffffff",
            activebackground="#333333",
            activeforeground="#00ff66",
        )
        self._wl_menu.add_command(
            label="🛡  Добавить в белый список", command=self._wl_add
        )
        self._wl_menu.add_command(
            label="Убрать из белого списка", command=self._wl_remove
        )
        self._wl_menu.add_separator()
        self._wl_menu.add_command(label="📂 Открыть папку", command=self._open_parent)
        self.table.set_context_callback(self._on_context_menu)

        self.table.tree.bind(
            "<<TreeviewSelect>>", lambda e: self._update_sel_info(), add="+"
        )
        self.table.tree.bind(
            "<ButtonRelease-1>",
            lambda e: self.after(30, self.filter_bar.refresh),
            add="+",
        )

        # --- Низ
        row = customtkinter.CTkFrame(right_panel, fg_color="transparent")
        row.pack(side="bottom", fill="x", pady=(6, 0))

        bottom = customtkinter.CTkFrame(right_panel, fg_color="transparent")
        bottom.pack(side="bottom", fill="x", pady=(6, 0))

        mid.pack(side="top", fill="both", expand=True, pady=(0, 6))

        self.progress_bar = customtkinter.CTkProgressBar(
            bottom,
            progress_color="#00ff66",
            fg_color="#252525",
            height=10,
            corner_radius=5,
        )
        self.progress_bar.pack(fill="x", pady=(0, 6))
        self.progress_bar.set(0)

        self.status_var = tk.StringVar(
            value="Нажмите «⚡ Начать проверку» для глобального анализа директории."
        )
        customtkinter.CTkLabel(
            bottom,
            textvariable=self.status_var,
            font=("Segoe UI", 10),
            text_color="#888888",
            wraplength=600,
            justify="left",
        ).pack(anchor="w")

        # (row уже упакован выше — просто наполняем)
        self.lbl_sum = tk.StringVar(value="")
        customtkinter.CTkLabel(
            row,
            textvariable=self.lbl_sum,
            text_color="#60cdff",
            font=("Segoe UI", 11, "bold"),
        ).pack(side="left")

        # Экспорт
        self.btn_run = customtkinter.CTkButton(
            row,
            text="⚡ Проверить",
            font=("Segoe UI", 11, "bold"),
            text_color="#000000",
            fg_color="#00ff66",
            hover_color="#00cc55",
            corner_radius=6,
            height=30,
            width=130,
            command=self.run_check,
        )
        self.btn_run.pack(side="right", padx=2)

        self.btn_delete = customtkinter.CTkButton(
            row,
            text="🗑 Очистить",
            font=("Segoe UI", 11, "bold"),
            fg_color="#1e1e1e",
            hover_color="#333333",
            corner_radius=6,
            height=30,
            width=130,
            state="disabled",
            command=self.delete_selected,
        )
        self.btn_delete.pack(side="right", padx=2)

        self.btn_cancel = customtkinter.CTkButton(
            row,
            text="✕",
            font=("Segoe UI", 11, "bold"),
            fg_color="#1e1e1e",
            hover_color="#333333",
            corner_radius=6,
            height=30,
            width=40,
            state="disabled",
            command=self.cancel_scan,
        )
        self.btn_cancel.pack(side="right", padx=2)

        self.btn_recycle = customtkinter.CTkButton(
            row,
            text="Корзина",
            font=("Segoe UI", 10),
            fg_color="#1e1e1e",
            hover_color="#333333",
            corner_radius=6,
            height=30,
            width=80,
            command=self._open_recycle_bin,
        )
        self.btn_recycle.pack(side="right", padx=2)

        self.btn_export = customtkinter.CTkButton(
            row,
            text="📄 Отчёт",
            font=("Segoe UI", 10),
            fg_color="#1e1e1e",
            hover_color="#333333",
            corner_radius=6,
            height=30,
            width=90,
            state="disabled",
            command=self.export_report,
        )
        self.btn_export.pack(side="right", padx=2)

    # =====================================================================
    # Сканирование
    # =====================================================================

    def run_check(self):
        if self.busy:
            return
        root = self.picker.get() or os.path.expanduser("~")
        opts = self.profile_sel.get_options()

        self._set_busy(True)
        self.table.clear()
        self.row_index.clear()
        self.result = []
        self.status_var.set("Запуск глубокого многопоточного сканирования…")
        self.progress_bar.set(0)
        for name, var in self.stat_vars.items():
            var.set(f"{name}: Вычисляется…")

        def work(cancel, q):
            return run_full_check(
                cancel,
                q,
                root=root,
                min_dup_mb=opts["min_dup_mb"],
                big_min_mb=opts["big_min_mb"],
                top=opts["top"],
                skip_duplicates=opts["skip_duplicates"],
                skip_empty=opts["skip_empty"],
                skip_big=opts["skip_big"],
            )

        self.cancel = common.run_task(self, work, self._on_msg)

    def _on_msg(self, kind, payload, _handle):
        if kind == "progress":
            self.status_var.set(payload)
            if "Шаг" in payload:
                try:
                    step = int(payload.split("/5")[0][-1])
                    self.progress_bar.set(step / 5.0)
                except Exception:
                    pass
        elif kind == "done":
            self.progress_bar.set(1.0)
            data = payload
            if data.get("cancelled"):
                self.status_var.set("Проверка отменена.")
                self._finish()
                return
            self.result = data["result"]
            self._last_summary = data.get("summary", {})
            self._fill(self._last_summary)
        elif kind == "error":
            self.progress_bar.set(0)
            common.show_error("Ошибка проверки", payload)
            self._finish()

    # =====================================================================
    # Наполнение
    # =====================================================================

    def _refill_from_cache(self):
        if self.result:
            self._fill(self._last_summary)

    def _fill(self, summary: dict):
        self._last_summary = summary
        self.table.clear()
        self.row_index.clear()
        show_prot = self.show_protected.get()

        for i, item in enumerate(self.result):
            path = item.get("path", "")
            is_prot = self._is_protected(item)
            if is_prot and not show_prot:
                continue

            iid = f"row{i}"
            checked = bool(item.get("auto", True)) and not is_prot
            kind = item["kind"]
            label = KIND_LABELS.get(kind, kind) + (" 🛡" if is_prot else "")
            size = int(item.get("size", 0) or 0)

            self.table.add_item(
                (iid, label, path, human_size(size)),
                checked=checked,
                kind=kind,
                protected=is_prot,
                sort_keys={
                    "type": label.lower(),
                    "path": path.lower(),
                    "size": size,
                },
                extra={"row": item},
            )
            self.row_index[iid] = {
                "path": path,
                "size": size,
                "kind": kind,
                "meta": item.get("meta", {}),
                "protected": is_prot,
            }

        self.table.commit()
        self.filter_bar.refresh()

        # Карта памяти
        ext_stats = summary.get("extension_stats", {})
        for name, var in self.stat_vars.items():
            var.set(f"• {name}: {human_size(ext_stats.get(name, 0))}")

        total_size = summary.get("total_size", 0)
        self.lbl_sum.set(f"Найдено мусора: {human_size(total_size)}")
        self.status_var.set("Анализ завершён. Нажмите «Очистить выбранное».")
        self._finish()
        if self.result:
            self.btn_delete.configure(state="normal")
            self.btn_export.configure(state="normal")

    def _is_protected(self, item: dict) -> bool:
        path = item.get("path", "")
        size = int(item.get("size", 0))
        if self.whitelist.contains(path):
            return True
        try:
            if judge_file(path, size).verdict == FileVerdict.PROTECTED:
                return True
        except Exception:
            pass
        return False

    # =====================================================================
    # ПКМ
    # =====================================================================

    def _on_context_menu(self, iid: str, x_root: int, y_root: int):
        if iid not in self.row_index:
            return
        try:
            self._wl_menu.tk_popup(x_root, y_root)
        finally:
            self._wl_menu.grab_release()

    def _wl_add(self):
        sel = self.table.tree.selection()
        if not sel:
            return
        row = self.row_index.get(sel[0])
        if not row:
            return
        self.whitelist.add(row["path"])
        common.show_info("Белый список", f"«{row['path']}» добавлен в белый список.")
        self._refill_from_cache()

    def _wl_remove(self):
        sel = self.table.tree.selection()
        if not sel:
            return
        row = self.row_index.get(sel[0])
        if not row:
            return
        self.whitelist.remove(row["path"])
        common.show_info("Белый список", f"«{row['path']}» убран из белого списка.")
        self._refill_from_cache()

    def _open_parent(self):
        sel = self.table.tree.selection()
        if not sel:
            return
        row = self.row_index.get(sel[0])
        if not row:
            return
        p = row["path"]
        target = p if os.path.isdir(p) else os.path.dirname(p)
        try:
            os.startfile(target)
        except Exception as e:
            common.show_error("Ошибка", f"Не удалось открыть папку: {e}")

    # =====================================================================
    # Экспорт отчёта
    # =====================================================================

    def export_report(self):
        if not self.result:
            common.show_info("Нет данных", "Сначала выполните анализ.")
            return

        # Спрашиваем формат
        from tkinter import messagebox

        # Простой диалог через messagebox.askyesnocancel:
        # Yes=HTML, No=CSV, Cancel=отмена
        ans = messagebox.askyesnocancel(
            "Экспорт отчёта",
            "Сохранить как HTML (откроется в браузере)?\n\n"
            "• Да — HTML\n"
            "• Нет — CSV (Excel)\n"
            "• Отмена — выйти",
        )
        if ans is None:
            return

        try:
            if ans:
                path = default_report_path("html")
                write_html_report(
                    path,
                    title="Отчёт DiskCleaner",
                    root_path=self.picker.get() or os.path.expanduser("~"),
                    profile=self.profile_sel.get_key(),
                    summary=self._last_summary,
                    items=self.result,
                    cancelled=False,
                )
                open_in_browser(path)
                common.show_info(
                    "Отчёт готов", f"HTML-отчёт сохранён:\n{path}\n\nОткрыт в браузере."
                )
            else:
                path = default_report_path("csv")
                write_csv_report(path, self.result)
                common.show_info("Отчёт готов", f"CSV-отчёт сохранён:\n{path}")
        except Exception as e:
            common.show_error("Ошибка экспорта", f"{type(e).__name__}: {e}")

    # =====================================================================
    # Удаление
    # =====================================================================

    def delete_selected(self):
        if self.busy:
            return
        checked = self.table.checked_items()
        if not checked:
            common.show_info("Ничего не выбрано", "Отметьте объекты для удаления.")
            return

        selected_items: list[dict] = []
        for iid in checked:
            row = self.row_index.get(iid)
            if not row or row["protected"]:
                continue
            selected_items.append(
                {
                    "path": row["path"],
                    "kind": row["kind"],
                    "size": row["size"],
                    "meta": row["meta"],
                }
            )

        if not selected_items:
            common.show_info(
                "Ничего не выбрано",
                "Все отмеченные объекты защищены или в белом списке.",
            )
            return

        if self.dry_run.get():
            preview = build_preview(selected_items, whitelist=self.whitelist)
            dlg = common.PreviewDialog(self, preview, title="Пробный прогон")
            self.wait_window(dlg)
            self.status_var.set("Пробный прогон завершён — ничего не удалено.")
            return

        preview = build_preview(selected_items, whitelist=self.whitelist)
        dlg = common.PreviewDialog(self, preview, title="Подтверждение удаления")
        self.wait_window(dlg)
        if not dlg.result:
            return

        to_recycle_paths: list[str] = []
        to_recycle_dirs: list[str] = []
        junk_keys: list[str] = []
        static_sizes: dict[str, int] = {}
        for it in selected_items:
            static_sizes[it["path"]] = it["size"]
            if it["kind"] == "junk":
                key = it["meta"].get("key")
                if key:
                    junk_keys.append(key)
            elif it["kind"] in ("duplicate", "big"):
                to_recycle_paths.append(it["path"])
            elif it["kind"] == "empty":
                to_recycle_dirs.append(it["path"])

        self._set_busy(True)
        self.status_var.set("Запуск удаления…")
        self.btn_delete.configure(state="disabled")

        self.deletion_log = DeletionLog()

        def work(cancel, q):
            freed = 0
            removed = 0
            errors: list[str] = []

            if junk_keys and not cancel.is_set():
                cleaner = SystemCleaner()
                res = cleaner.clean(junk_keys, to_recycle=True, cancel=cancel)
                freed += res["freed"]
                removed += res["removed_files"]
                errors.extend(res["errors"])
                for key in junk_keys:
                    self.deletion_log.record(
                        DeletionEntry(
                            path=f"[junk:{key}]",
                            kind="junk_group",
                            size=0,
                            ts=_time.time(),
                            to_recycle=True,
                            success=True,
                        )
                    )

            if to_recycle_paths and not cancel.is_set():
                bad = move_to_recycle(to_recycle_paths)
                bad_set = set(bad)
                for p in to_recycle_paths:
                    ok = p not in bad_set
                    sz = static_sizes.get(p, 0)
                    if ok:
                        freed += sz
                        removed += 1
                    self.deletion_log.record(
                        DeletionEntry(
                            path=p,
                            kind="file",
                            size=sz,
                            ts=_time.time(),
                            to_recycle=True,
                            success=ok,
                            error="" if ok else "заблокирован",
                        )
                    )
                for p in bad:
                    request_delete_on_reboot(p)
                if bad:
                    errors.append(f"Заблокировано: {len(bad)} файлов")

            if to_recycle_dirs and not cancel.is_set():
                bad_dirs = move_to_recycle(to_recycle_dirs)
                bad_set = set(bad_dirs)
                for p in to_recycle_dirs:
                    ok = p not in bad_set
                    if ok:
                        removed += 1
                    self.deletion_log.record(
                        DeletionEntry(
                            path=p,
                            kind="dir",
                            size=0,
                            ts=_time.time(),
                            to_recycle=True,
                            success=ok,
                            error="" if ok else "заблокирован",
                        )
                    )
                if bad_dirs:
                    errors.append(f"Заблокировано папок: {len(bad_dirs)}")

            return {"freed": freed, "removed": removed, "errors": errors}

        self.cancel = common.run_task(self, work, self._on_delete_msg)

    def _on_delete_msg(self, kind, payload, _handle):
        if kind == "done":
            res = payload
            msg = (
                f"Освобождено: {human_size(res['freed'])}\n"
                f"Удалено объектов: {res['removed']}"
            )
            if res.get("errors"):
                msg += "\n\nПредупреждения:\n" + "\n".join(
                    f"• {e}" for e in res["errors"][:5]
                )
            msg += "\n\nВсе перемещённые файлы лежат в Корзине."
            common.show_info("Очистка завершена", msg)
            self._finish()
            self.lbl_sum.set("")
            self.status_var.set(
                "Готово. Нажмите «⚡ Начать проверку» для нового анализа."
            )
            self.result = []
            self.row_index.clear()
            self.table.clear()
            self.btn_export.configure(state="disabled")
            for name, var in self.stat_vars.items():
                var.set(f"• {name}: 0 Б")
        elif kind == "error":
            common.show_error("Ошибка", payload)
            self._finish()

    def _open_recycle_bin(self):
        try:
            os.startfile("shell:RecycleBinFolder")
        except Exception as e:
            common.show_error("Ошибка", f"Не удалось открыть Корзину: {e}")

    # =====================================================================
    # Прочее
    # =====================================================================

    def _update_sel_info(self):
        try:
            s = self.table.stats()
            self.status_var.set(
                f"Выбрано: {s['checked']} · Общий вес: {human_size(s['size_checked'])}"
            )
        except tk.TclError:
            pass

    def cancel_scan(self):
        if self.cancel is not None:
            self.cancel.stop()
            self.status_var.set("Отмена операции…")
            self.btn_cancel.configure(state="disabled")

    def _finish(self):
        self._set_busy(False)
        self.cancel = None

    def _set_busy(self, busy: bool):
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.btn_run.configure(state=state)
        self.btn_cancel.configure(state="normal" if busy else "disabled")
        if not busy and self.result:
            self.btn_delete.configure(state="normal")
