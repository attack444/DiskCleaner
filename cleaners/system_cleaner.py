"""Поиск и очистка «мусорных» мест Windows с поддержкой многопоточности."""

from __future__ import annotations

import glob
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import utils

SAFE_AGE_THRESHOLD = 24 * 60 * 60


@dataclass
class JunkCategory:
    key: str
    title: str
    roots: list[str] = field(default_factory=list)
    note: str = ""
    kind: str = "dirs"

    def size_info(self) -> tuple[int, int]:
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
        parts = [r for r in self.roots if r and os.path.isdir(r)]
        return "; ".join(parts) if parts else "не найдено"


def _env_path(var: str, sub: str = "") -> str | None:
    base = os.environ.get(var)
    if not base:
        return None
    return os.path.join(base, sub) if sub else base


def _find_firefox_caches() -> list[str]:
    result: list[str] = []
    for base_var in ("APPDATA", "LOCALAPPDATA"):
        base = os.environ.get(base_var)
        if not base:
            continue
        pattern = os.path.join(base, "Mozilla", "Firefox", "Profiles", "*", "cache2")
        result.extend(glob.glob(pattern))
    return result


def _browser_cache_roots(vendor: str, product: str) -> list[str]:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return []
    base = os.path.join(local, vendor, product, "User Data")
    roots = []
    for sub in ("Cache", "Code Cache", "GPUCache"):
        roots.append(os.path.join(base, "Default", sub))
        pattern = os.path.join(base, "Profile *", sub)
        roots.extend(glob.glob(pattern))
    return [r for r in roots if os.path.isdir(r)]


# --------------------------------------------------------------------------
# Пакетная очистка каталога
# --------------------------------------------------------------------------


def _purge_directory_contents(
    root: str, to_recycle: bool, cancel=None
) -> tuple[int, int, list[str]]:
    """Очищает содержимое одного каталога. Возвращает (freed, removed, errors)."""
    freed = 0
    removed = 0
    errors: list[str] = []

    if cancel is not None and cancel.is_set():
        return 0, 0, []

    files: list[str] = []
    dirs: list[str] = []
    current_time = time.time()

    try:
        with os.scandir(root) as it:
            for entry in it:
                if cancel is not None and cancel.is_set():
                    break
                try:
                    if entry.is_symlink():
                        continue
                    st = entry.stat(follow_symlinks=False)
                    if current_time - st.st_mtime < SAFE_AGE_THRESHOLD:
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        dirs.append(entry.path)
                    else:
                        files.append(entry.path)
                except OSError:
                    continue
    except OSError as exc:
        return 0, 0, [f"{root}: {exc.strerror or exc}"]

    # ---- Файлы — одним пакетом ----
    if files and not (cancel and cancel.is_set()):
        sizes: dict[str, int] = {}
        for f in files:
            try:
                sizes[f] = os.path.getsize(f)
            except OSError:
                sizes[f] = 0

        bad = (
            utils.move_to_recycle(files)
            if to_recycle
            else utils.delete_permanent(files)
        )
        bad_set = set(bad)
        for f in files:
            if f not in bad_set:
                freed += sizes[f]
                removed += 1

        if bad:
            deferred = 0
            for failed in bad:
                if utils.request_delete_on_reboot(failed):
                    deferred += 1
            unhandled = bad[deferred:]
            if unhandled:
                max_show = 8
                if len(unhandled) <= max_show:
                    errors.extend(
                        [f"Заблокировано: {os.path.basename(p)}" for p in unhandled]
                    )
                else:
                    shown = [os.path.basename(p) for p in unhandled[:max_show]]
                    errors.append(
                        f"Заблокировано {len(unhandled)} файлов: "
                        + ", ".join(shown)
                        + ", …"
                    )

    # ---- Папки — поштучно (чтобы одна проблемная не срывала всё) ----
    for d in dirs:
        if cancel is not None and cancel.is_set():
            break

        size_before = utils.dir_size(d)[0]
        bad = utils.move_to_recycle([d]) if to_recycle else utils.delete_permanent([d])

        if not bad:
            freed += size_before
            removed += 1
            continue

        # Внутри папки что-то держит файл — рекурсивно чистим содержимое
        inner_f, inner_r, inner_e = _purge_directory_contents(d, to_recycle, cancel)
        freed += inner_f
        removed += inner_r
        errors.extend(inner_e)

        try:
            with os.scandir(d) as it:
                empty_now = not any(True for _ in it)
            if empty_now:
                bad2 = (
                    utils.move_to_recycle([d])
                    if to_recycle
                    else utils.delete_permanent([d])
                )
                if not bad2:
                    removed += 1
                else:
                    errors.extend(bad2)
        except OSError:
            pass

    return freed, removed, errors


# --------------------------------------------------------------------------
# SystemCleaner
# --------------------------------------------------------------------------


class SystemCleaner:
    def __init__(self) -> None:
        self.categories: list[JunkCategory] = self._build_categories()

    @staticmethod
    def _build_categories() -> list[JunkCategory]:
        sys_root = os.environ.get("SystemRoot", r"C:\Windows")
        local_app = os.environ.get("LOCALAPPDATA", "")

        return [
            JunkCategory(
                key="user_temp",
                title="Временные файлы пользователя (%TEMP%)",
                roots=[_env_path("TEMP")] + [_env_path("TMP")],
                note="Файлы приложений, остатки установщиков и кэш сессий.",
            ),
            JunkCategory(
                key="system_temp",
                title="Системные временные файлы (Windows\\Temp)",
                roots=[os.path.join(sys_root, "Temp")],
                note="Требуются права Администратора. Логи системных служб.",
            ),
            JunkCategory(
                key="update_cache",
                title="Кэш Windows Update (SoftwareDistribution)",
                roots=[os.path.join(sys_root, "SoftwareDistribution", "Download")],
                note="Скачанные файлы обновлений ОС, которые уже установлены.",
            ),
            JunkCategory(
                key="delivery_optimization",
                title="Кэш Оптимизации доставки Windows",
                roots=[
                    os.path.join(
                        sys_root,
                        "ServiceProfiles",
                        "NetworkService",
                        "AppData",
                        "Local",
                        "Microsoft",
                        "Windows",
                        "DeliveryOptimization",
                        "Cache",
                    )
                ],
                note="Временные файлы раздачи обновлений в локальной сети.",
            ),
            JunkCategory(
                key="shader_cache",
                title="Кэш шейдеров (DirectX / NVIDIA / AMD)",
                roots=[
                    os.path.join(local_app, "D3DSCache"),
                    os.path.join(local_app, "NVIDIA", "DXCache"),
                    os.path.join(local_app, "NVIDIA", "GLCache"),
                    os.path.join(local_app, "AMD", "DxCache"),
                ],
                note="Скомпилированные кэш-файлы графики из игр и 3D-программ.",
            ),
            JunkCategory(
                key="font_cache",
                title="Кэш шрифтов Windows (DirectWrite)",
                roots=[
                    os.path.join(
                        sys_root,
                        "ServiceProfiles",
                        "LocalService",
                        "AppData",
                        "Local",
                        "FontCache",
                    )
                ],
                note="Временные файлы кэширования системных и пользовательских шрифтов.",
            ),
            JunkCategory(
                key="crash_dumps",
                title="Дампы сбоев (CrashDumps)",
                roots=[_env_path("LOCALAPPDATA", "CrashDumps")],
                note="Отчеты об ошибках упавших программ. Занимают много места.",
            ),
            JunkCategory(
                key="memory_dumps",
                title="Аварийные дампы памяти (BSOD)",
                roots=[os.path.join(sys_root, "Minidump")],
                note="Логи синих экранов смерти. Нужны только для диагностики.",
            ),
            JunkCategory(
                key="chrome_cache",
                title="Кэш Google Chrome",
                roots=_browser_cache_roots("Google", "Chrome"),
                note="Кэш, Code Cache, GPUCache всех профилей.",
            ),
            JunkCategory(
                key="edge_cache",
                title="Кэш Microsoft Edge",
                roots=_browser_cache_roots("Microsoft", "Edge"),
            ),
            JunkCategory(
                key="yandex_cache",
                title="Кэш Яндекс.Браузера",
                roots=_browser_cache_roots("Yandex", "YandexBrowser"),
                note="Кэш страниц, иконок и скриптов.",
            ),
            JunkCategory(
                key="opera_cache",
                title="Кэш браузера Opera",
                roots=[
                    os.path.join(local_app, "Opera Software", "Opera Stable", "Cache"),
                    os.path.join(
                        local_app, "Opera Software", "Opera GX Stable", "Cache"
                    ),
                ],
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
                note="Только thumbcache_.db / iconcache_.db",
                kind="thumbcache",
            ),
            JunkCategory(
                key="recycle",
                title="Корзина Windows",
                roots=[],
                kind="recycle",
            ),
        ]

    # ------------------------------------------------------------------
    # Анализ
    # ------------------------------------------------------------------

    def analyze(self, keys: list[str] | None = None, progress_cb=None) -> list[dict]:
        result = []
        cats = [c for c in self.categories if keys is None or c.key in keys]

        def scan_worker(cat_obj):
            size, files = cat_obj.size_info()
            return {
                "key": cat_obj.key,
                "title": cat_obj.title,
                "size": size,
                "files": files,
                "roots": cat_obj.describe_roots(),
                "note": cat_obj.note,
                "kind": cat_obj.kind,
                "exists": cat_obj.exists() or cat_obj.kind == "recycle",
            }

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(scan_worker, cat) for cat in cats]
            for i, future in enumerate(futures):
                res_dict = future.result()
                result.append(res_dict)
                if progress_cb:
                    progress_cb(i + 1, len(cats), res_dict["title"])
        return result

    # ------------------------------------------------------------------
    # Очистка
    # ------------------------------------------------------------------

    def clean(
        self, keys: list[str], to_recycle: bool = True, progress_cb=None, cancel=None
    ) -> dict:
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
                _f, _r, _e = self._clean_thumbcache(cat, to_recycle, cancel=cancel)
                freed += _f
                removed += _r
                errors.extend(_e)
            else:
                for root in cat.roots:
                    if not (root and os.path.isdir(root)):
                        continue
                    if cancel is not None and cancel.is_set():
                        break
                    _f, _r, _e = _purge_directory_contents(
                        root, to_recycle, cancel=cancel
                    )
                    freed += _f
                    removed += _r
                    errors.extend(_e)

            if progress_cb:
                progress_cb(i + 1, len(cats), cat.title)

        return {"freed": freed, "removed_files": removed, "errors": errors}

    @staticmethod
    def _clean_thumbcache(
        cat: JunkCategory, to_recycle: bool, cancel=None
    ) -> tuple[int, int, list[str]]:
        freed = 0
        removed = 0
        errors: list[str] = []
        targets: list[str] = []
        current_time = time.time()

        for root in cat.roots:
            if cancel is not None and cancel.is_set():
                break
            if not (root and os.path.isdir(root)):
                continue
            try:
                with os.scandir(root) as it:
                    for entry in it:
                        if cancel is not None and cancel.is_set():
                            break
                        low = entry.name.lower()
                        if not (
                            (
                                low.startswith("thumbcache")
                                or low.startswith("iconcache_")
                            )
                            and low.endswith(".db")
                        ):
                            continue
                        try:
                            if (
                                current_time
                                - entry.stat(follow_symlinks=False).st_mtime
                                < SAFE_AGE_THRESHOLD
                            ):
                                continue
                        except OSError:
                            pass
                        targets.append(entry.path)
            except OSError:
                continue

        if not targets:
            return 0, 0, []

        sizes: dict[str, int] = {}
        for p in targets:
            try:
                sizes[p] = os.path.getsize(p)
            except OSError:
                sizes[p] = 0

        bad = (
            utils.move_to_recycle(targets)
            if to_recycle
            else utils.delete_permanent(targets)
        )

        # Thumbcache — восстанавливаемый кэш, при неудаче «в корзину» смело удаляем безвозвратно
        if to_recycle and bad:
            bad2 = utils.delete_permanent(bad)
            errors.extend(bad2)
            ok = [p for p in targets if p not in bad2]
        else:
            errors.extend(bad)
            ok = [p for p in targets if p not in bad]

        for p in ok:
            freed += sizes[p]
            removed += 1

        return freed, removed, errors
