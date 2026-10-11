"""Слой безопасности: журнал удалений, восстановление, dry-run, whitelist."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field


# =====================================================================
# Журнал удалений
# =====================================================================


@dataclass
class DeletionEntry:
    path: str
    kind: str  # "file" | "dir" | "junk_group" | "duplicates"
    size: int
    ts: float
    to_recycle: bool
    success: bool
    error: str = ""


def _log_dir() -> str:
    base = os.path.join(os.environ.get("TEMP", tempfile.gettempdir()), "DiskCleaner")
    os.makedirs(base, exist_ok=True)
    return base


class DeletionLog:
    """Персистентный журнал (JSONL). Один запуск = новый файл журнала."""

    MAX_ENTRIES_IN_MEMORY = 5000

    def __init__(self, session_id: str | None = None):
        self.session_id = session_id or time.strftime("%Y%m%d_%H%M%S")
        self.path = os.path.join(_log_dir(), f"deletions_{self.session_id}.jsonl")
        self.entries: list[DeletionEntry] = []

    def record(self, entry: DeletionEntry) -> None:
        self.entries.append(entry)
        if len(self.entries) > self.MAX_ENTRIES_IN_MEMORY:
            # Держим в памяти только хвост — на диск всё уже ушло
            self.entries = self.entries[-self.MAX_ENTRIES_IN_MEMORY :]
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
        except OSError as e:
            logging.error(f"[DeletionLog] не удалось записать запись: {e}")

    def recent_success(self, kind_filter: str | None = None) -> list[DeletionEntry]:
        return [
            e
            for e in self.entries
            if e.success and (kind_filter is None or e.kind == kind_filter)
        ]

    def total_freed_this_session(self) -> int:
        return sum(e.size for e in self.entries if e.success)


def list_recent_sessions(max_sessions: int = 10) -> list[str]:
    """Возвращает список session_id последних сессий удалений."""
    base = _log_dir()
    try:
        files = sorted(
            (
                f
                for f in os.listdir(base)
                if f.startswith("deletions_") and f.endswith(".jsonl")
            ),
            reverse=True,
        )
    except OSError:
        return []
    return [f[len("deletions_") : -len(".jsonl")] for f in files[:max_sessions]]


def load_session(session_id: str) -> list[DeletionEntry]:
    path = os.path.join(_log_dir(), f"deletions_{session_id}.jsonl")
    entries: list[DeletionEntry] = []
    if not os.path.isfile(path):
        return entries
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(DeletionEntry(**json.loads(line)))
                except Exception:
                    continue
    except OSError:
        pass
    return entries


def restore_last_session() -> tuple[int, int]:
    """Возвращает (restored, failed). Работает только с recycle=True (не permanent)."""
    sessions = list_recent_sessions(1)
    if not sessions:
        return 0, 0
    entries = load_session(sessions[0])
    candidates = [
        e
        for e in entries
        if e.success and e.to_recycle and os.path.exists(e.path) is False
    ]
    # Восстановление из Корзины — пока только через ручное «Открыть Корзину»
    # Полноценный API восстановления — отдельная задача (см. roadmap wave 3)
    return 0, len(candidates)


# =====================================================================
# Whitelist (постоянный)
# =====================================================================


class Whitelist:
    """Список путей, которые пользователь запретил трогать навсегда."""

    def __init__(self, path: str | None = None):
        self.path = path or os.path.join(_log_dir(), "whitelist.json")
        self._paths: set[str] = set()
        self._load()

    def _load(self) -> None:
        if not os.path.isfile(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self._paths = {os.path.normcase(p) for p in data if isinstance(p, str)}
        except Exception as e:
            logging.error(f"[Whitelist] не удалось загрузить: {e}")

    def save(self) -> None:
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(sorted(self._paths), f, ensure_ascii=False, indent=2)
        except OSError as e:
            logging.error(f"[Whitelist] не удалось сохранить: {e}")

    def add(self, path: str) -> None:
        self._paths.add(os.path.normcase(os.path.abspath(path)))
        self.save()

    def remove(self, path: str) -> None:
        self._paths.discard(os.path.normcase(os.path.abspath(path)))
        self.save()

    def all(self) -> list[str]:
        return sorted(self._paths)

    def contains(self, path: str) -> bool:
        p = os.path.normcase(os.path.abspath(path))
        if p in self._paths:
            return True
        for w in self._paths:
            if p.startswith(w.rstrip("\\/") + os.sep):
                return True
        return False


# =====================================================================
# Dry-run и preview
# =====================================================================


@dataclass
class PreviewItem:
    path: str
    kind: str
    size: int
    protected: bool = False
    reason: str = ""


@dataclass
class Preview:
    items: list[PreviewItem] = field(default_factory=list)
    total_size: int = 0
    protected_count: int = 0
    protected_size: int = 0

    def __bool__(self) -> bool:
        return bool(self.items)


def build_preview(items: list[dict], whitelist: Whitelist | None = None) -> Preview:
    """Принимает список «выбранных» элементов из UI и возвращает сводку.

    Каждый item — это dict с ключами: path, kind, size (как в full_check).
    """
    preview = Preview()
    wl = whitelist or Whitelist()

    for item in items:
        path = item.get("path") or ""
        if not path:
            continue
        size = int(item.get("size") or 0)
        kind = item.get("kind") or ""

        protected = False
        reason = ""
        if wl.contains(path):
            protected = True
            reason = "В вашем белом списке"
        # Здесь же можно дёрнуть smart_rules.judge_file, если он доступен
        try:
            from .smart_rules import FileVerdict, judge_file

            j = judge_file(path, size)
            if j.verdict == FileVerdict.PROTECTED:
                protected = True
                reason = j.reason
        except Exception:
            pass

        preview.items.append(
            PreviewItem(
                path=path,
                kind=kind,
                size=size,
                protected=protected,
                reason=reason,
            )
        )
        preview.total_size += size
        if protected:
            preview.protected_count += 1
            preview.protected_size += size

    return preview
