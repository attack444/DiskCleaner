"""Вкладка «🔄 Автообновление драйверов»."""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk

from cleaners import driver_auto as da
from cleaners import drivers
from cleaners import utils
from ui import common

KIND_LABELS = {
    "gpu": "Видео",
    "audio": "Звук",
    "network": "Сеть",
    "wifi": "Wi-Fi",
    "chipset": "Чипсет",
    "storage": "Накопители",
    "usb": "USB",
}


class DriverAutoTab(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=8)
        self.cancel = None
        self.busy = False
        self.states: list[dict] = []
        self.rows: list[dict] = []   
        self._build()

    # ------------------------------------------------------------------
    def _build(self):
        header = ttk.Label(
            self,
            text="Обновление драйверов без регистрации и сторонних программ:\n"
                 "• Видеокарты NVIDIA — полная автоматизация: официальный драйвер "
                 "скачивается с сервера NVIDIA и устанавливается в один клик.\n"
                 "• Остальное железо (звук, сеть, чипсет, USB) обновляется "
                 "штатно через Windows Update.\n"
                 "Ссылки на сайты с обязательной регистрацией больше не нужны.",
            justify="left")
        header.pack(anchor="w")

        btns = ttk.Frame(self)
        btns.pack(fill="x", pady=6)
        self.btn_scan = ttk.Button(btns, text="Проверить обновления",
                                   command=self.run_scan)
        self.btn_scan.pack(side="left")
        self.btn_dl = ttk.Button(btns, text="⬇ Скачать и установить (NVIDIA)",
                                 command=self.download_selected, state="disabled")
        self.btn_dl.pack(side="left", padx=4)
        self.btn_wu = ttk.Button(btns, text="Проверить в Windows Update",
                                 command=self.check_wu, state="disabled")
        self.btn_wu.pack(side="left", padx=4)
        self.btn_wu_open = ttk.Button(btns, text="Открыть Центр обновления",
                                      command=self.open_wu)
        self.btn_wu_open.pack(side="left", padx=4)
        self.btn_cancel = ttk.Button(btns, text="Отмена", command=self.cancel_scan,
                                     state="disabled")
        self.btn_cancel.pack(side="right")

        mid = ttk.LabelFrame(self, text="Компоненты (отметьте для обновления)",
                             padding=6)
        mid.pack(fill="both", expand=True, pady=4)
        cols = [("check", "", 36), ("cat", "Тип", 90), ("name", "Устройство", 330),
                ("ver", "Установлено", 130), ("status", "Статус", 240)]
        tf = ttk.Frame(mid)
        tf.pack(fill="both", expand=True)
        self.table = common.CheckTable(tf, cols, stretch_col=2, height=14)
        self.table.tree.pack(side="left", fill="both", expand=True)
        ysb = ttk.Scrollbar(tf, orient="vertical", command=self.table.tree.yview)
        ysb.pack(side="right", fill="y")
        self.table.tree.configure(yscrollcommand=ysb.set)

        dl_frame = ttk.Frame(self)
        dl_frame.pack(fill="x", pady=(6, 0))
        self.dl_var = tk.StringVar(value="")
        ttk.Label(dl_frame, textvariable=self.dl_var, foreground="#1a4f8a",
                  wraplength=1000, justify="left").pack(anchor="w")
        
        # ИСПРАВЛЕНО: Режим determinate жестко задан изначально, переключать его на лету нельзя
        self.pbar = ttk.Progressbar(dl_frame, mode="determinate", maximum=100)
        self.pbar.pack(fill="x", pady=(2, 0))

        self.status_var = tk.StringVar(
            value="Нажмите «Проверить обновления» — приложение найдёт ваше "
                  "оборудование и сверит версии драйверов.")
        ttk.Label(self, textvariable=self.status_var, foreground="#666666",
                  wraplength=1000, justify="left").pack(anchor="w", pady=(4, 0))
        self.sum_var = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.sum_var,
                  foreground="#1a6d1a").pack(anchor="w")

    # ------------------------------------------------------------------
    def run_scan(self):
        if self.busy:
            return
        self._set_busy(True)
        self.table.clear()
        self.rows = []
        self.states = []
        self.dl_var.set("")
        self.pbar.config(value=0)
        self.sum_var.set("")
        self.status_var.set("Сканирование оборудования и драйверов… (может занять до минуты)")

        def work(cancel, q):
            def progress(text):
                q.put(("progress", text))
            return da.scan_driver_status(progress_cb=progress, cancel=cancel)

        self.cancel = common.run_task(self, work, self._on_msg)

    def _on_msg(self, kind, payload, _cancel):
        if kind == "progress":
            self.status_var.set(payload)
        elif kind == "done":
            self.states = payload or []
            self._fill()
        elif kind == "error":
            common.show_error("Ошибка проверки", payload)
            self._finish()

    def _fill(self):
        self.table.clear()
        self.rows = []
        n_update = n_current = n_wu = n_error = 0
        for i, st in enumerate(self.states):
            iid = f"s{i}"
            mode = st.get("mode", da.MODE_ERROR)
            if mode == da.MODE_UPDATE:
                n_update += 1
            elif mode == da.MODE_CURRENT:
                n_current += 1
            elif mode == da.MODE_WU or mode == da.MODE_MANUAL:
                n_wu += 1
            elif mode == da.MODE_ERROR:
                n_error += 1
                
            checked = (mode == da.MODE_UPDATE and st.get("vendor") == "nvidia")
            kind_label = KIND_LABELS.get(st.get("kind", ""), st.get("kind", ""))
            ver = st.get("installed_version") or "—"
            
            # ИСПРАВЛЕНО: Безопасный парсинг даты для исключения пустых срезов
            if st.get("installed_date") and len(st["installed_date"]) == 8:
                d = st["installed_date"]
                ver += f" ({d[:4]}.{d[4:6]}.{d[6:]})"
                
            self.table.add_item(
                (iid, kind_label, st.get("name", ""), ver,
                 st.get("status_text", "")),
                checked=checked)
            self.rows.append({"iid": iid, "state": st})

        parts = [f"Компонентов: {len(self.states)}"]
        if n_update:
            parts.append(f"обновлений NVIDIA: {n_update}")
        if n_current:
            parts.append(f"актуальны: {n_current}")
        if n_wu:
            parts.append(f"через Windows Update: {n_wu}")
        if n_error:
            parts.append(f"ошибок: {n_error}")
        self.sum_var.set(" · ".join(parts) + ".")

        self.btn_dl.config(state="normal" if n_update else "disabled")
        self.btn_wu.config(state="normal" if self.states else "disabled")
        
        if n_update:
            self.status_var.set(f"Найдено обновлений NVIDIA: {n_update}. Нажмите «Скачать и установить (NVIDIA)» — остальное обновит Windows Update.")
        elif n_error:
            self.status_var.set("Проверка завершена с ошибками. Для остальных компонентов нажмите «Проверить в Windows Update».")
        else:
            self.status_var.set("Проверка завершена. Для компонентов (звук, сеть, чипсет и др.) нажмите «Проверить в Windows Update».")
        self._finish()

    # ------------------------------------------------------------------
    def _selected_states(self) -> list[dict]:
        checked = self.table.checked_items()
        return [r["state"] for r in self.rows if r["iid"] in checked]

    def download_selected(self):
        if self.busy:
            return
        sel = [s for s in self._selected_states()
               if s.get("mode") == da.MODE_UPDATE and s.get("candidate")]
        if not sel:
            common.show_info("Ничего не выбрано", "Нет отмеченных компонентов с доступным автоматическим обновлением.")
            return

        lines = []
        for s in sel:
            cand = s["candidate"]
            lines.append(f"• {s.get('name', '')} → {cand.get('version', '')}")
        msg = ("Будет скачано с официального сайта NVIDIA и установлено:\n\n"
               + "\n".join(lines) +
               "\n\nФайл большой (сотни МБ) — загрузка займёт время.\n"
               "Во время установки появится запрос подтверждения Windows (UAC).\nПродолжить?")
        if not common.confirm(self, "Скачать и установить драйверы", msg):
            return

        self._set_busy(True)
        self.btn_dl.config(state="disabled")
        self.pbar.config(value=0)
        self.dl_var.set("Подготовка…")
        self.status_var.set("Сканирование официальных драйверов…")

        def work(cancel, q):
            result = {"ok": [], "failed": [], "canceled": False}
            for s in sel:
                if cancel.is_set():
                    result["canceled"] = True
                    break
                cand = s["candidate"]
                name = s.get("name", "устройство")
                fname = cand.get("filename") or "driver.exe"
                dest = os.path.join(da.default_download_dir(), fname)
                q.put(("progress", f"Скачивание: {name} ({cand.get('version', '')})…"))
                
                try:
                    if os.path.exists(dest) and os.path.getsize(dest) > 1024 * 1024:
                        q.put(("progress", "Файл уже скачан, пропускаем загрузку."))
                    else:
                        da.download_file(
                            cand["url"], dest,
                            progress_cb=lambda d, t, sp: q.put(("download", (d, t, sp))),
                            cancel=cancel)
                    
                    q.put(("progress", f"Установка драйвера NVIDIA {cand.get('version', '')}…"))
                    ok, msg = da.run_installer(dest)
                    
                    if ok:
                        q.put(("progress", f"✓ {name}: {msg}"))
                        result["ok"].append(name)
                    else:
                        q.put(("progress", f"✗ {name}: {msg}"))
                        result["failed"].append((name, msg))
                        
                except da.DownloadCanceled:
                    result["canceled"] = True
                    break
                except da.NetworkError as exc:
                    result["failed"].append((name, str(exc)))
                    q.put(("progress", f"✗ {name}: {exc}"))
                except Exception as exc:
                    result["failed"].append((name, f"{type(exc).__name__}: {exc}"))
                    q.put(("progress", f"✗ {name}: {exc}"))
            return result

        self.cancel = common.run_task(self, work, self._on_dl_msg)

    def _on_dl_msg(self, kind, payload, _cancel):
        if kind == "progress":
            self.status_var.set(payload)
        elif kind == "download":
            done, total, speed = payload
            if total:
                self.pbar.config(maximum=total, value=done)
                pct = done * 100.0 / total
                txt = f"Скачано: {utils.human_size(done)} из {utils.human_size(total)} ({pct:.0f}%)"
            else:
                txt = f"Скачано: {utils.human_size(done)}"
                
            if speed and speed > 0:
                try:
                    txt += f" · {utils.human_size(int(speed))}/с"
                except (ValueError, TypeError):
                    pass
            self.dl_var.set(txt)
        elif kind == "done":
            res = payload
            ok = res.get("ok", [])
            failed = res.get("failed", [])
            canceled = res.get("cancelled", False) or res.get("canceled", False)
            
            self.dl_var.set("")
            self.pbar.config(value=0)
            lines = []
            if ok:
                lines.append("Установлено успешно (рекомендуется перезагрузка):\n" + "\n".join("• " + x for x in ok))
            for name, err in failed:
                lines.append(f"• {name}: {err}")
                
            msg = "\n".join(lines) if lines else "Операция отменена."
            if canceled:
                self.status_var.set("Операция прервана.")
            else:
                self.status_var.set("Готово. Проверьте результат установки.")
                common.show_info("Результат установки", msg)
            self._finish()
            self.btn_dl.config(state="normal")
        elif kind == "error":
            common.show_error("Ошибка", payload)
            self._finish()

    def check_wu(self):
        if self.busy:
            return
        self._set_busy(True)
        self.status_var.set("Запуск проверки Центра обновления Windows…")
        self.sum_var.set("")
        self.dl_var.set("")
        
        def work(cancel, q):
            def progress(text):
                q.put(("progress", text))
            return drivers.check_windows_update(progress_cb=progress, cancel=cancel)
            
        self.cancel = common.run_task(self, work, self._on_wu_msg)

    def _on_wu_msg(self, kind, payload, _cancel):
        if kind == "progress":
            self.status_var.set(payload)
        elif kind == "done":
            res = payload
            self._finish()
            if res.get("started"):
                self.status_var.set("Проверка обновлений запущена. Windows ищет драйверы в фоне — откройте Центр обновления и нажмите «Установить», если что-то найдено.")
                self.sum_var.set("Открыть: «Центр обновления Windows» → «Просмотреть дополнительные обновления» → «Обновления драйверов».")
                common.show_info("Windows Update", "Проверка обновлений запущена.\n\nОткройте Центр обновления Windows и проверьте раздел «Дополнительные обновления → Обновления драйверов» — там появятся найденные драйверы (если есть).")
                drivers.open_windows_update_settings()
            else:
                self.status_var.set("Не удалось запустить проверку автоматически. Откройте Центр обновления Windows вручную.")
                self.sum_var.set((res.get("output") or "")[:400])
                common.show_error("Windows Update", "Не удалось запустить проверку обновлений автоматически.\nПричина: " + ((res.get("output") or "неизвестно")[:300]))
        elif kind == "error":
            self._finish()
            common.show_error("Ошибка", payload)

    def open_wu(self):
        if drivers.open_windows_update_settings():
            self.status_var.set("Открыт Центр обновления Windows.")
        else:
            common.show_error("Не удалось", "Не удалось открыть Параметры Windows.")

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
        if busy:
            self.btn_dl.config(state="disabled")
            self.btn_wu.config(state="disabled")
        else:
            has_update = any(
                r["state"].get("mode") == da.MODE_UPDATE
                and r["state"].get("vendor") == "nvidia"
                for r in self.rows)
            self.btn_dl.config(state="normal" if has_update else "disabled")
            self.btn_wu.config(state="normal" if self.states else "disabled")
