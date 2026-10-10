"""Поиск и очистка «мусорных» мест Windows:

пользовательские/системные временные файлы, кэш браузеров, корзина и т.д.
"""
from __future__ import annotations

import os
import glob
from dataclasses import dataclass, field

from . import utils


@dataclass
class JunkCategory:
    key: str
    title: str
    roots: list[str] = field(default_factory=list)
    note: str = ""
    kind: str = "dirs"          # 'dirs' — почистить содержимое корней, 'recycle' — корзина

    def size_info(self) -> tuple[int, int]:
        """Возвращает (суммарный размер, число файлов) для существующих корней."""
        total_size = 0
        total_files = 0
        if self.kind == "recycle":
            return utils.recycle_bin_size(), 0
        for root in self.roots:
            if root and os.path.isdir(root):
                s, f = utils.dir_size(root)
                total_size += s
                total_files += f
        return total_size, total_files

    def exists(self) -> bool:
        if self.kind == "recycle":
            return True
        return any(r and os.path.isdir(r) for r in self.roots)

    def describe_roots(self) -> str:
        if self.kind == "recycle":
            return "Корзина Windows"
        parts = []
        for r in self.roots:
            if r and os.path.isdir(r):
                parts.append(r)
        return "; ".join(parts) if parts else "не найдено"


def _env_path(var: str, sub: str = "") -> str | None:
    base = os.environ.get(var)
    if not base:
        return None
    p = os.path.join(base, sub) if sub else base
    return p


def _find_firefox_caches() -> list[str]:
    """Кэш Firefox лежит в каталогах cache2 профилей (может быть в AppData и Local)."""
    result: list[str] = []
    for base_var in ("APPDATA", "LOCALAPPDATA"):
        base = os.environ.get(base_var)
        if not base:
            continue
        pattern = os.path.join(base, "Mozilla", "Firefox", "Profiles", "*", "cache2")
        result.extend(glob.glob(pattern))
    return result


class SystemCleaner:
    """Знает обо всех категориях мусора и умеет их очищать."""

    def __init__(self) -> None:
        self.categories: list[JunkCategory] = self._build_categories()

    @staticmethod
    def _build_categories() -> list[JunkCategory]:
        sys_root = os.environ.get("SystemRoot", r"C:\Windows")
        cats = [
            JunkCategory(
                key="user_temp",
                title="Временные файлы пользователя (%TEMP%)",
                roots=[_env_path("TEMP")] + [_env_path("TMP")],
                note="Файлы приложений, установщиков и т.п.",
            ),
            JunkCategory(
                key="system_temp",
                title="Системные временные файлы (Windows\\Temp)",
                roots=[os.path.join(sys_root, "Temp")],
                note="Может потребовать прав администратора.",
            ),
            JunkCategory(
                key="update_cache",
                title="Кэш Windows Update",
                roots=[os.path.join(sys_root, "SoftwareDistribution", "Download")],
                note="Скачанные обновления, уже установленные.",
            ),
            JunkCategory(
                key="crash_dumps",
                title="Дампы сбоев (CrashDumps)",
                roots=[_env_path("LOCALAPPDATA", "CrashDumps")],
            ),
            JunkCategory(
                key="memory_dumps",
                title="Аварийные дампы памяти",
                roots=[os.path.join(sys_root, "Minidump")],
            ),
            JunkCategory(
                key="chrome_cache",
                title="Кэш Google Chrome",
                roots=_browser_cache_roots("Google", "Chrome"),
                note="Кэш, Code Cache, GPUCache профилей.",
            ),
            JunkCategory(
                key="edge_cache",
                title="Кэш Microsoft Edge",
                roots=_browser_cache_roots("Microsoft", "Edge"),
            ),
            JunkCategory(
                key="firefox_cache",
                title="Кэш Mozilla Firefox",
                roots=_find_firefox_caches(),
            ),
            JunkCategory(
                key="thumbnail_cache",
                title="Кэш эскизов (Explorer)",
                roots=[_env_path("LOCALAPPDATA", "Microsoft\\Windows\\Explorer")],
                note="Только thumbcache_*.db / iconcache_*.db",
                kind="thumbcache",
            ),
            JunkCategory(
                key="recycle",
                title="Корзина",
                roots=[],
                kind="recycle",
            ),
        ]
        return cats

    def analyze(self, keys: list[str] | None = None, progress_cb=None) -> list[dict]:
        result = []
        cats = [c for c in self.categories if keys is None or c.key in keys]
        for i, cat in enumerate(cats):
            size, files = cat.size_info()
            result.append({
                "key": cat.key,
                "title": cat.title,
                "size": size,
                "files": files,
                "roots": cat.describe_roots(),
                "note": cat.note,
                "kind": cat.kind,
                "exists": cat.exists() or cat.kind == "recycle",
            })
            if progress_cb:
                progress_cb(i + 1, len(cats), cat.title)
        return result

    def clean(self, keys: list[str], to_recycle: bool = True,
              progress_cb=None, cancel=None) -> dict:
        freed = 0
        removed = 0
        errors: list[str] = []

        cats = [c for c in self.categories if c.key in keys]
        for i, cat in enumerate(cats):
            if cancel is not None and cancel.is_set():
                break
            if cat.kind == "recycle":
                size_before = utils.recycle_bin_size()
                if size_before > 0:
                    if utils.empty_recycle_bin():
                        freed += size_before
                        removed += 1
                    else:
                        errors.append("Корзина: не удалось очистить")
            elif cat.kind == "thumbcache":
                _f, _r, _e = self._clean_thumbcache(cat, to_recycle)
                freed += _f
                removed += _r
                errors.extend(_e)
            else:
                for root in cat.roots:
                    if not (root and os.path.isdir(root)):
                        continue
                    _f, _r, _e = _purge_directory_contents(root, to_recycle, cancel=cancel)
                    freed += _f
                    removed += _r
                    errors.extend(_e)
            if progress_cb:
                progress_cb(i + 1, len(cats), cat.title)

        return {"freed": freed, "removed_files": removed, "errors": errors}

    @staticmethod
    def _clean_thumbcache(cat: JunkCategory, to_recycle: bool) -> tuple[int, int, list[str]]:
        freed = 0
        removed = 0
        errors: list[str] = []
        targets: list[str] = []
        for root in cat.roots:
            if not (root and os.path.isdir(root)):
                continue
            try:
                names = os.listdir(root)
            except OSError:
                continue
            for name in names:
                low = name.lower()
                if (low.startswith("thumbcache_") or low.startswith("iconcache_")) \
                        and low.endswith(".db"):
                    targets.append(os.path.join(root, name))
        if not targets:
            return 0, 0, []

        # Замеряем точный размер файлов ДО их фактического удаления
        files_sizes = {}
        for p in targets:
            try:
                files_sizes[p] = os.path.getsize(p)
            except OSError:
                files_sizes[p] = 0

        bad = utils.move_to_recycle(targets) if to_recycle else utils.delete_permanent(targets)
        if to_recycle and bad:
            bad2 = utils.delete_permanent(bad)
            errors.extend(bad2)
            
            # Считаем место на основе кэшированных размеров
            for p in targets:
                if p not in bad2:
                    freed += files_sizes[p]
                    removed += 1
        else:
            errors.extend(bad)
            for p in targets:
                if p not in bad:
                    freed += files_sizes[p]
                    removed += 1
        return freed, removed, errors


def _browser_cache_roots(vendor: str, product: str) -> list[str]:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return []
    base = os.path.join(local, vendor, product, "User Data")
    roots = []
    for sub in ("Cache", "Code Cache", "GPUCache"):
        roots.append(os.path.join(base, "Default", sub))
        # ИСПРАВЛЕНО: паттерн изменен с "Profile" на "Profile *", чтобы корректно находить папки профилей
        pattern = os.path.join(base, "Profile *", sub)
        roots.extend(glob.glob(pattern))
    return [r for r in roots if os.path.isdir(r)]


def _purge_directory_contents(root: str, to_recycle: bool, cancel=None) -> tuple[int, int, list[str]]:
    """Удаляет всё содержимое каталога root (сам root остаётся)."""
    freed = 0
    removed = 0
    errors: list[str] = []
    
    if cancel is not None and cancel.is_set():
        return 0, 0, []

    try:
        names = sorted(os.listdir(root))
    except OSError as exc:
        return 0, 0, [f"{root}: {exc.strerror or exc}"]

    files: list[str] = []
    dirs: list[str] = []
    for name in names:
        full = os.path.join(root, name)
        try:
            if os.path.isdir(full) and not os.path.islink(full):
                dirs.append(full)
            else:
                files.append(full)
        except OSError:
            files.append(full)

       # === Продолжение функции _purge_directory_contents ===
    
    # Работа с файлами
    if files and not (cancel and cancel.is_set()):
        files_sizes = {}
        for f in files:
            try:
                files_sizes[f] = os.path.getsize(f)
            except OSError:
                files_sizes[f] = 0

        if to_recycle:
            bad = utils.move_to_recycle(files)
        else:
            bad = utils.delete_permanent(files)
            
        ok = [f for f in files if f not in bad]
        removed += len(ok)
        for f in ok:
            freed += files_sizes[f]  # Берем сохраненный размер
        errors.extend(_short_errors(bad, "файл"))

    # Работа с папками
    for d in dirs:
        if cancel is not None and cancel.is_set():
            break
            
        size_before = utils.dir_size(d)[0]
        if to_recycle:
            bad = utils.move_to_recycle([d])
        else:
            bad = utils.delete_permanent([d])
            
        if not bad:
            freed += size_before
            removed += 1
        else:
            # Рекурсивный спуск с передачей cancel
            inner_f, inner_r, inner_e = _purge_directory_contents(d, to_recycle, cancel)
            freed += inner_f
            removed += inner_r
            errors.extend(inner_e)
            
            try:
                if not os.listdir(d):
                    bad = utils.move_to_recycle([d]) if to_recycle else utils.delete_permanent([d])
                    if not bad:
                        removed += 1
                    else:
                        errors.extend(bad)
            except OSError:
                pass
                
    return freed, removed, errors


def _short_errors(paths: list[str], kind: str, max_show: int = 8) -> list[str]:
    if not paths:
        return []
    if len(paths) <= max_show:
        return [f"Не удалось удалить {kind}: {os.path.basename(p)}" for p in paths]
    shown = [os.path.basename(p) for p in paths[:max_show]]
    return [f"Не удалось удалить {len(paths)} {kind}ов: " + ", ".join(shown) + ", ..."]
