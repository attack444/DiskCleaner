"""Вкладка «🛠 Драйверы»: список устройств, диагностика, обновление через Windows Update."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from cleaners import drivers
from ui import common


class DriversTab(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=8)
        self.cancel = None
        self.busy = False
        self.devices: list[dict] = []
        self.driver_pkgs: list[dict] = []
        self._build()

    # ------------------------------------------------------------------
    def _build(self):
        header = ttk.Label(
            self,
            text="Устройства и драйверы вашего ПК.\n"
                 "Проверка обновлений выполняется через Центр обновления Windows "
                 "(рекомендуемый и безопасный способ).",
            justify="left")
        header.pack(anchor="w")

        # Кнопки управления
        btns = ttk.Frame(self)
        btns.pack(fill="x", pady=6)
        self.btn_devices = ttk.Button(btns, text="Показать устройства",
                                      command=self.scan_devices)
        self.btn_devices.pack(side="left")
        self.btn_drivers = ttk.Button(btns, text="Список пакетов драйверов",
                                      command=self.scan_drivers)
        self.btn_drivers.pack(side="left", padx=4)
        self.btn_wu = ttk.Button(btns, text="Проверить обновления (Windows Update)",
                                 command=self.check_updates)
        self.btn_wu.pack(side="left", padx=4)
        self.btn_wu_view = ttk.Button(btns, text="Открыть Центр обновления",
                                      command=self.open_wu)
        self.btn_wu_view.pack(side="left", padx=4)
        self.btn_cancel = ttk.Button(btns, text="Отмена", command=self.cancel_scan,
                                     state="disabled")
        self.btn_cancel.pack(side="right")

        # Ноутбук с двумя таблицами
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, pady=6)

        # --- вкладка «Устройства» ---
        dev_frame = ttk.Frame(self.nb)
        self.nb.add(dev_frame, text="Устройства (PnP)")
        dev_cols = [("check", "", 36), ("status", "Статус", 90),
                    ("name", "Устройство", 380), ("class", "Класс", 150),
                    ("id", "Instance ID", 330)]
        tf = ttk.Frame(dev_frame)
        tf.pack(fill="both", expand=True, padx=4, pady=4)
        self.dev_table = common.CheckTable(tf, dev_cols, stretch_col=2, height=15)
        self.dev_table.tree.pack(side="left", fill="both", expand=True)
        ys = ttk.Scrollbar(tf, orient="vertical", command=self.dev_table.tree.yview)
        ys.pack(side="right", fill="y")
        self.dev_table.tree.configure(yscrollcommand=ys.set)

        # --- вкладка «Драйверы» ---
        drv_frame = ttk.Frame(self.nb)
        self.nb.add(drv_frame, text="Пакеты драйверов (pnputil)")
        drv_cols = [("check", "", 36), ("name", "Драйвер", 340),
                    ("provider", "Поставщик", 150), ("version", "Версия", 110),
                    ("date", "Дата", 90)]
        tf2 = ttk.Frame(drv_frame)
        tf2.pack(fill="both", expand=True, padx=4, pady=4)
        self.drv_table = common.CheckTable(tf2, drv_cols, stretch_col=1, height=15)
        self.drv_table.tree.pack(side="left", fill="both", expand=True)
        ys2 = ttk.Scrollbar(tf2, orient="vertical", command=self.drv_table.tree.yview)
        ys2.pack(side="right", fill="y")
        self.drv_table.tree.configure(yscrollcommand=ys2.set)

        # --- статус-бар ---
        self.status_var = tk.StringVar(
            value="Нажмите «Показать устройства», чтобы увидеть состояние оборудования.")
        ttk.Label(self, textvariable=self.status_var, foreground="#666666",
                  wraplength=900, justify="left").pack(anchor="w", pady=(4, 0))
        self.sum_var = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.sum_var, foreground="#1a6d1a").pack(anchor="w")

    # ------------------------------------------------------------------
    def scan_devices(self):
        if self.busy:
            return
        self._set_busy(True)
        self.dev_table.clear()
        self.status_var.set("Чтение списка устройств (Get-PnpDevice)…")

        def work(cancel, q):
            def progress(n, name):
                q.put(("progress", f"Обработано устройств: {n}"))
            return drivers.get_pnp_devices(progress_cb=progress, cancel=cancel)

        self.cancel = common.run_task(self, work, self._on_devices_msg)

    def _on_devices_msg(self, kind, payload, _cancel):
        if kind == "progress":
            self.status_var.set(payload)
        elif kind == "done":
            self.devices = payload
            self._fill_devices()
        elif kind == "error":
            common.show_error("Ошибка", payload)
            self._finish()

    def _fill_devices(self):
        self.dev_table.clear()
        self.dev_table.tree.tag_configure("problem", foreground="#b00020")
        
        for i, d in enumerate(self.devices):
            status = (d.get("status") or "—")
            problem = (d.get("problem") or "")
            name = d.get("name") or "(без имени)"
            st_txt = status
            tags = ()
            
            if problem and problem not in ("CM_PROB_NONE", "0"):
                st_txt = f"⚠ {problem}"
                tags = ("problem",)
                
            iid = f"dev{i}"
            # ИСПРАВЛЕНО: Таблица заполняется через add_item вместо прямого insert, 
            # что чинит работу чекбоксов и регистрацию строк в индексе
            self.dev_table.add_item(
                (iid, st_txt, name, d.get("class") or "—", d.get("instance_id") or ""),
                checked=False
            )
            # Применяем теги оформления к добавленной строке
            self.dev_table.tree.item(iid, tags=tags)
            
        self._update_summary()
        self._finish()

    def _update_summary(self):
        s = drivers.device_status_summary(self.devices)
        problems = len(s["problems"]) + len(s["missing"])
        if problems:
            self.sum_var.set(
                f"Всего устройств: {s['total']} · работают: {s['ok']} · "
                f"требуют внимания: {problems}.\nРекомендуется проверить обновления "
                "драйверов (кнопка «Проверить обновления»).")
        else:
            self.sum_var.set(f"Всего устройств: {s['total']} · проблем не обнаружено.")
        if not self.devices:
            self.sum_var.set("Не удалось получить список устройств (нужны права?).")
        self.status_var.set(f"Показано устройств: {len(self.devices)}. "
                            "Двойной клик по строке — детали." if self.devices
                            else "Устройств нет.")

    # ------------------------------------------------------------------
    def scan_drivers(self):
        if self.busy:
            return
        self._set_busy(True)
        self.drv_table.clear()
        self.status_var.set("Чтение пакетов драйверов (pnputil)…")

        def work(cancel, q):
            def progress(n, name):
                q.put(("progress", f"Обработано пакетов: {n}"))
            return drivers.get_driver_packages(progress_cb=progress, cancel=cancel)

        self.cancel = common.run_task(self, work, self._on_drivers_msg)

    def _on_drivers_msg(self, kind, payload, _cancel):
        if kind == "progress":
            self.status_var.set(payload)
        elif kind == "done":
            self.driver_pkgs = payload
            self._fill_drivers()
        elif kind == "error":
            common.show_error("Ошибка", payload)
            self._finish()

    def _fill_drivers(self):
        self.drv_table.clear()
        for i, d in enumerate(self.driver_pkgs):
            name = d.get("original") or d.get("name") or "—"
            self.drv_table.add_item(
                (f"drv{i}", name,
                 d.get("provider") or "—",
                 d.get("version") or "—",
                 d.get("date") or "—"),
                checked=False)
        self._finish()
        self.status_var.set(
            f"Пакетов драйверов: {len(self.driver_pkgs)}. "
            "Полный список драйверов и их версии видны здесь." if self.driver_pkgs
            else "Не удалось получить пакеты драйверов (возможно, нужны права администратора).")

    # ------------------------------------------------------------------
    def check_updates(self):
        if self.busy:
            return
        self._set_busy(True)
        self.status_var.set("Проверка обновлений через Windows Update…")
        self.sum_var.set("")

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
            if res.get("started"):
                self.status_var.set(
                    "Проверка обновлений запущена. Windows ищет драйверы и обновления "
                    "в фоне; результат появится в Центре обновления.")
                self.sum_var.set("Откройте «Центр обновления Windows» (кнопка ниже) "
                                 "и нажмите «Установить», если обновления найдены.")
            else:
                self.status_var.set(
                    "Не удалось запустить проверку автоматически. "
                    "Откройте Центр обновления Windows вручную.")
                self.sum_var.set(res.get("output", "")[:400])
            self._finish()
        elif kind == "error":
            common.show_error("Ошибка", payload)
            self._finish()

       # === Окончание класса DriversTab в файле ui/drivers_tab.py ===

    def open_wu(self):
        """Открывает штатные параметры Центра обновления Windows."""
        if drivers.open_windows_update_settings():
            self.status_var.set("Открыт Центр обновления Windows.")
        else:
            common.show_error("Не удалось", "Не удалось открыть Параметры Windows.")

    # ------------------------------------------------------------------
    def cancel_scan(self):
        """Отменяет текущую асинхронную операцию сканирования."""
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
        self.btn_devices.config(state=state)
        self.btn_drivers.config(state=state)
        self.btn_wu.config(state=state)
        self.btn_wu_view.config(state=state)
        self.btn_cancel.config(state="normal" if busy else "disabled")
