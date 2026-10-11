"""Анализатор занятого места: список + treemap на Canvas."""

from __future__ import annotations  # ← обязательно первой строкой после docstring

import os
import tkinter as tk

import customtkinter

from cleaners.utils import dir_size, human_size
from ui import common  # ← импорт на уровне модуля, не отложенный


# ---------------------------------------------------------------------
# Squarified treemap (адаптированный алгоритм Bruls/Huizing/van Wijk)
# ---------------------------------------------------------------------


def _worst(row, row_sum, short):
    if not row or row_sum <= 0 or short <= 0:
        return float("inf")
    m = max(row)
    n = min(row)
    s2 = short * short
    rs2 = row_sum * row_sum
    return max(s2 * m / rs2, rs2 / (s2 * n))


def _squarify_rec(areas, indices, x, y, w, h, result):
    if not areas or not indices or w <= 0 or h <= 0:
        return
    if len(areas) == 1:
        result[indices[0]] = (x, y, w, h)
        return

    short = min(w, h)
    i = 1
    row_sum = areas[0]
    best = _worst([areas[0]], row_sum, short)
    while i < len(areas):
        new_sum = row_sum + areas[i]
        new_best = _worst(areas[: i + 1], new_sum, short)
        if new_best > best:
            break
        row_sum = new_sum
        best = new_best
        i += 1

    row = areas[:i]
    row_idx = indices[:i]

    if w >= h:
        strip_w = row_sum / h if h > 0 else 0
        cy = y
        for a, idx in zip(row, row_idx):
            rh = a / strip_w if strip_w > 0 else 0
            result[idx] = (x, cy, strip_w, rh)
            cy += rh
        _squarify_rec(areas[i:], indices[i:], x + strip_w, y, w - strip_w, h, result)
    else:
        strip_h = row_sum / w if w > 0 else 0
        cx = x
        for a, idx in zip(row, row_idx):
            rw = a / strip_h if strip_h > 0 else 0
            result[idx] = (cx, y, rw, strip_h)
            cx += rw
        _squarify_rec(areas[i:], indices[i:], x, y + strip_h, w, h - strip_h, result)


def squarify(sizes: list[float], x: float, y: float, w: float, h: float):
    result = [(0, 0, 0, 0)] * len(sizes)
    if not sizes or w <= 0 or h <= 0:
        return result
    total = sum(sizes)
    if total <= 0:
        return result
    areas = [s * w * h / total for s in sizes]
    _squarify_rec(areas, list(range(len(sizes))), x, y, w, h, result)
    return result


# ---------------------------------------------------------------------
# Цвета для treemap
# ---------------------------------------------------------------------

_PALETTE = [
    "#2e7d32",
    "#1b5e20",
    "#388e3c",
    "#43a047",
    "#00897b",
    "#00695c",
    "#00796b",
    "#26a69a",
    "#1565c0",
    "#0d47a1",
    "#1976d2",
    "#1e88e5",
    "#6a1b9a",
    "#4a148c",
    "#8e24aa",
    "#ab47bc",
    "#ef6c00",
    "#e65100",
    "#f57c00",
    "#fb8c00",
    "#c62828",
    "#b71c1c",
    "#d32f2f",
    "#e53935",
]


def color_for(idx: int, depth: int = 0) -> str:
    base = _PALETTE[idx % len(_PALETTE)]
    if depth <= 0:
        return base
    # немного темнее с каждым уровнем
    r = int(base[1:3], 16)
    g = int(base[3:5], 16)
    b = int(base[5:7], 16)
    factor = max(0.4, 1.0 - 0.15 * depth)
    r, g, b = int(r * factor), int(g * factor), int(b * factor)
    return f"#{r:02x}{g:02x}{b:02x}"


# ---------------------------------------------------------------------
# Вкладка
# ---------------------------------------------------------------------


class AnalyzerTab(customtkinter.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.cancel: common.TaskHandle | None = None
        self.busy = False
        self.current_path = os.path.expanduser("~")
        self.items: list[dict] = []  # {"path":..., "size":..., "is_dir":...}
        self._rects: list[tuple[int, int, int, int, dict]] = []
        self._build()
        self.after(200, self.rescan)

    def _build(self):
        top = customtkinter.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=6, pady=(4, 6))

        customtkinter.CTkLabel(
            top,
            text="Цель:",
            font=("Segoe UI", 10, "bold"),
        ).pack(side="left", padx=(0, 6))

        self.path_var = tk.StringVar(value=self.current_path)
        self.path_entry = customtkinter.CTkEntry(
            top,
            textvariable=self.path_var,
            height=28,
            corner_radius=6,
            border_color="#333333",
        )
        self.path_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))

        customtkinter.CTkButton(
            top,
            text="Обзор…",
            width=80,
            height=28,
            corner_radius=6,
            fg_color="#1e1e1e",
            hover_color="#333333",
            command=self._browse,
        ).pack(side="left", padx=2)
        customtkinter.CTkButton(
            top,
            text="⬆ Вверх",
            width=80,
            height=28,
            corner_radius=6,
            fg_color="#1e1e1e",
            hover_color="#333333",
            command=self._go_up,
        ).pack(side="left", padx=2)
        self.btn_scan = customtkinter.CTkButton(
            top,
            text="⚡ Сканировать",
            width=130,
            height=28,
            corner_radius=6,
            font=("Segoe UI", 11, "bold"),
            text_color="#000000",
            fg_color="#00ff66",
            hover_color="#00cc55",
            command=self.rescan,
        )
        self.btn_scan.pack(side="left", padx=2)

        body = customtkinter.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=6, pady=(0, 4))

        # Левая колонка — список
        left = customtkinter.CTkFrame(
            body, corner_radius=8, border_width=1, border_color="#333333", width=380
        )
        left.pack(side="left", fill="y", padx=(0, 6))
        left.pack_propagate(False)

        customtkinter.CTkLabel(
            left,
            text="Содержимое",
            font=("Segoe UI", 11, "bold"),
            text_color="#00ff66",
        ).pack(anchor="w", padx=10, pady=(6, 2))

        self.lb = tk.Listbox(
            left,
            bg="#252525",
            fg="#e8e8e8",
            selectbackground="#1b5e20",
            selectforeground="#ffffff",
            borderwidth=0,
            highlightthickness=0,
            font=("Consolas", 10),
            activestyle="none",
        )
        self.lb.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.lb.bind("<Double-Button-1>", lambda e: self._drill_down())

        # Правая колонка — treemap
        right = customtkinter.CTkFrame(
            body, corner_radius=8, border_width=1, border_color="#333333"
        )
        right.pack(side="right", fill="both", expand=True)

        self.canvas = tk.Canvas(
            right, bg="#1a1a1a", highlightthickness=0, borderwidth=0
        )
        self.canvas.pack(fill="both", expand=True, padx=4, pady=4)
        self.canvas.bind("<Configure>", lambda e: self._draw_treemap())
        self.canvas.bind("<Double-Button-1>", self._on_canvas_double_click)
        self.canvas.bind("<Motion>", self._on_canvas_motion)
        self.canvas.bind("<Leave>", lambda e: self.status_var.set(""))

        self.status_var = tk.StringVar(value="Двойной клик по папке — войти внутрь.")
        customtkinter.CTkLabel(
            self,
            textvariable=self.status_var,
            font=("Segoe UI", 10),
            text_color="#888888",
            wraplength=1000,
            justify="left",
        ).pack(anchor="w", padx=10, pady=(4, 6))

    # ------------------------------------------------------------------

    def _browse(self):
        from tkinter import filedialog

        path = filedialog.askdirectory(initialdir=self.current_path)
        if path:
            self.current_path = os.path.normpath(path)
            self.path_var.set(self.current_path)
            self.rescan()

    def _go_up(self):
        parent = os.path.dirname(self.current_path.rstrip("\\/"))
        if parent and parent != self.current_path:
            self.current_path = parent
            self.path_var.set(parent)
            self.rescan()

    def _drill_down(self, *_):
        sel = self.lb.curselection()
        if not sel:
            return
        idx = int(sel[0])
        if idx >= len(self.items):
            return
        it = self.items[idx]
        if it["is_dir"]:
            self.current_path = it["path"]
            self.path_var.set(self.current_path)
            self.rescan()
        else:
            try:
                os.startfile(os.path.dirname(it["path"]))
            except Exception:
                pass

    # ------------------------------------------------------------------

    def rescan(self):
        if self.busy:
            return

        path = self.path_var.get().strip()
        if not path or not os.path.isdir(path):
            common.show_error("Неверный путь", f"Папка не найдена: {path}")
            return
        self.current_path = path
        self._set_busy(True)
        self.lb.delete(0, tk.END)
        self.canvas.delete("all")
        self.items = []
        self.status_var.set(f"Сканирование {path}…")

        def work(cancel, q):
            out = []
            try:
                with os.scandir(path) as it:
                    entries = sorted(it, key=lambda e: e.name)
            except OSError as e:
                return out
            for i, entry in enumerate(entries, start=1):
                if cancel.is_set():
                    break
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_file(follow_symlinks=False):
                        size = entry.stat(follow_symlinks=False).st_size
                        out.append({"path": entry.path, "size": size, "is_dir": False})
                    elif entry.is_dir(follow_symlinks=False):
                        size, _ = dir_size(entry.path)
                        out.append({"path": entry.path, "size": size, "is_dir": True})
                except OSError:
                    continue
                if i % 5 == 0:
                    q.put(("progress", f"Обработано объектов: {i}"))
            return out

        self.cancel = common.run_task(self, work, self._on_msg)

    def _on_msg(self, kind, payload, _h):
        if kind == "progress":
            self.status_var.set(payload)
        elif kind == "done":
            self.items = sorted(payload or [], key=lambda x: x["size"], reverse=True)
            self._refresh_list()
            self._draw_treemap()
            total = sum(x["size"] for x in self.items)
            self.status_var.set(
                f"Объектов: {len(self.items)} · Суммарный размер: {human_size(total)}"
            )
            self._set_busy(False)
        elif kind == "error":
            from ui import common

            common.show_error("Ошибка", payload)
            self._set_busy(False)

    def _refresh_list(self):
        self.lb.delete(0, tk.END)
        for it in self.items[:500]:  # предохранитель от миллионов строк
            name = os.path.basename(it["path"].rstrip("\\/")) or it["path"]
            if it["is_dir"]:
                marker = "📁"
            else:
                marker = "📄"
            self.lb.insert(tk.END, f"{marker} {human_size(it['size']):>10}  {name}")

    # ------------------------------------------------------------------

    def _draw_treemap(self):
        self.canvas.delete("all")
        self._rects.clear()
        if not self.items:
            return

        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w < 20 or h < 20:
            return

        # Берём top-30 крупнейших + "Прочее"
        top_n = self.items[:30]
        rest = self.items[30:]
        if rest:
            rest_size = sum(x["size"] for x in rest)
            top_n = top_n + [
                {
                    "path": f"[+{len(rest)} мелких]",
                    "size": rest_size,
                    "is_dir": False,
                    "_rest": True,
                }
            ]

        sizes = [max(1.0, float(it["size"])) for it in top_n]
        rects = squarify(sizes, 2, 2, w - 4, h - 4)

        for idx, (rx, ry, rw, rh) in enumerate(rects):
            if rw <= 1 or rh <= 1:
                continue
            it = top_n[idx]
            color = color_for(idx) if not it.get("_rest") else "#444444"
            rect_id = self.canvas.create_rectangle(
                rx, ry, rx + rw, ry + rh, fill=color, outline="#111111", width=1
            )
            self._rects.append((int(rx), int(ry), int(rx + rw), int(ry + rh), it))

            # Подпись — только если места хватает
            if rw > 60 and rh > 26:
                name = os.path.basename(it["path"].rstrip("\\/")) or it["path"]
                short = name if len(name) < 24 else name[:21] + "…"
                self.canvas.create_text(
                    rx + 6,
                    ry + 6,
                    text=short,
                    anchor="nw",
                    fill="#ffffff",
                    font=("Segoe UI", 9, "bold"),
                )
                self.canvas.create_text(
                    rx + 6,
                    ry + 22,
                    text=human_size(it["size"]),
                    anchor="nw",
                    fill="#d0ffd0",
                    font=("Segoe UI", 8),
                )

    def _on_canvas_motion(self, event):
        for x1, y1, x2, y2, it in self._rects:
            if x1 <= event.x <= x2 and y1 <= event.y <= y2:
                self.status_var.set(
                    f"{it['path']} — {human_size(it['size'])}  "
                    f"(двойной клик, чтобы войти)"
                )
                return
        self.status_var.set("")

    def _on_canvas_double_click(self, event):
        for x1, y1, x2, y2, it in self._rects:
            if x1 <= event.x <= x2 and y1 <= event.y <= y2:
                if it.get("_rest"):
                    return
                if it["is_dir"]:
                    self.current_path = it["path"]
                    self.path_var.set(self.current_path)
                    self.rescan()
                else:
                    try:
                        os.startfile(os.path.dirname(it["path"]))
                    except Exception:
                        pass
                return

    def _set_busy(self, busy: bool):
        self.busy = busy
        self.btn_scan.configure(state="disabled" if busy else "normal")
