"""Интеллектуальные правила анализа пути и классификации файлов.

Перед тем как считать файл «мусором», «дубликатом» или «крупным объектом»,
система определяет КОНТЕКСТ: где лежит файл и чем он является.

Три исхода:
  PROTECTED — не предлагать к удалению вообще;
  CAUTION/USER_DATA — показать, но по умолчанию галочка СНЯТА;
  SAFE — показать и отметить автоматически.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum


# =====================================================================
# Перечисления
# =====================================================================


class PathContext(Enum):
    SYSTEM = "system"  # C:\Windows, System32
    PROGRAM_FILES = "program"  # установленный софт
    GAME_LIBRARY = "game"  # Steam/Epic/GOG — игровые данные
    DEV_TOOL = "devtool"  # Git, Python, node_modules, .venv, libexec
    TEMP = "temp"  # %TEMP%, Windows\Temp
    USER_DATA = "user"  # Документы, Загрузки, Рабочий стол
    USER_MEDIA = "media"  # Изображения, Видео, Музыка
    UNKNOWN = "unknown"


class FileVerdict(Enum):
    PROTECTED = "protected"  # нельзя даже предлагать
    CAUTION = "caution"  # показать, но НЕ отметить
    USER_DATA = "user_data"  # показать, но НЕ отметить
    SAFE = "safe"  # можно смело отметить


# =====================================================================
# Маркеры — точные имена компонентов пути (не подстроки!)
# =====================================================================

# Игровые библиотеки и лаунчеры
GAME_LIBRARY_DIRS = frozenset(
    {
        "steamapps",
        "steamlibrary",
        "epic games",
        "epicgameslauncher",
        "gog galaxy",
        "gog games",
        "gog.com",
        "battle.net",
        "blizzard entertainment",
        "riot games",
        "league of legends",
        "origin games",
        "ea games",
        "electronic arts",
        "ubisoft",
        "ubisoft game launcher",
        "uplay",
        "xboxgames",
        "windowsapps",
        "modifiablewindowsapps",
        "rockstar games",
        "social club",
        "bethesda.net launcher",
        "paradox interactive",
        "minecraft",
        "roblox",
        "steam",
    }
)

# Среды разработки и инструменты
DEV_TOOL_DIRS = frozenset(
    {
        "git",
        "libexec",
        "python",
        "python27",
        "python36",
        "python37",
        "python38",
        "python39",
        "python310",
        "python311",
        "python312",
        "python313",
        "node_modules",
        ".venv",
        "venv",
        ".tox",
        ".nox",
        "env",
        "site-packages",
        "dist-packages",
        ".git",
        ".hg",
        ".svn",
        ".cargo",
        ".rustup",
        ".nuget",
        ".gradle",
        ".m2",
        "msys2",
        "cygwin",
        "anaconda",
        "anaconda3",
        "miniconda3",
        "dotnet",
        "jdk",
        "jre",
        "java",
        "cmake",
        "llvm",
        "chocolatey",
        "scoop",
        "microsoft vs code",
        "jetbrains",
        "visual studio",
    }
)

# Расширения игровых ассетов — удаление сломает игру
GAME_ASSET_EXTS = frozenset(
    {
        # Unreal Engine
        ".pak",
        ".ucas",
        ".utoc",
        ".uasset",
        ".uexp",
        ".ubulk",
        ".sig",
        ".ushaderbytecode",
        # Unity
        ".assets",
        ".resource",
        ".unity3d",
        ".resS",
        # CD Projekt RED (Cyberpunk, Witcher)
        ".bundle",
        ".archive",
        ".rpkg",
        ".pack",
        # Bethesda (Skyrim, Fallout)
        ".bsa",
        ".ba2",
        ".esm",
        ".esp",
        ".esl",
        # Valve Source
        ".vpk",
        ".gma",
        # Quake/IdTech
        ".pk3",
        ".pk4",
        ".wad",
        ".mpq",
        # Общие контейнеры игр
        ".arc",
        ".big",
        ".forge",
        # Промежуточные данные рендера
        ".tfc",
        ".dds",
        # Видео-вставки
        ".bk2",
        ".bik",
        ".usm",
        ".vp6",
        # Аудио-банки
        ".fsb",
        ".bank",
        ".wwise",
        ".wwu",
    }
)

# Важные данные пользователя — базы, проекты, образы
IMPORTANT_DATA_EXTS = frozenset(
    {
        ".db",
        ".sqlite",
        ".sqlite3",
        ".mdb",
        ".accdb",
        ".sql",
        ".vmdk",
        ".vhd",
        ".vhdx",
        ".vdi",
        ".qcow2",
        ".iso",
        ".img",
        ".dmg",
        ".bak",
        ".backup",
        ".psd",
        ".ai",
        ".indd",
        ".aep",
        ".prproj",
        ".blend",
        ".max",
        ".c4d",
        ".fbx",
        ".sln",
        ".vcxproj",
        ".csproj",
    }
)

# Системные бинарники
SYSTEM_CRITICAL_EXTS = frozenset(
    {
        ".sys",
        ".drv",
        ".ocx",
        ".cpl",
        ".scr",
        ".msi",
        ".msp",
        ".cab",
    }
)

# Внутри игр — только эти папки считаются безопасным кэшем
_GAME_SAFE_SUBDIRS = (
    "\\cache\\",
    "\\logs\\",
    "\\crashes\\",
    "\\crashdumps\\",
    "\\shadercache\\",
    "\\temp\\",
    "\\downloading\\",
    "\\depotcache\\",
)


# =====================================================================
# Классификация пути
# =====================================================================

# Кэш нормализованных корней окружения
_SYS_ROOT: str | None = None
_USER_ROOT: str | None = None
_PROG_ROOTS: tuple[str, ...] = ()
_TEMP_ROOTS: tuple[str, ...] = ()


def _init_roots() -> None:
    global _SYS_ROOT, _USER_ROOT, _PROG_ROOTS, _TEMP_ROOTS
    if _SYS_ROOT is not None:
        return
    _SYS_ROOT = _norm(os.environ.get("SystemRoot", r"C:\Windows"))
    _USER_ROOT = _norm(os.environ.get("USERPROFILE", "")) or None
    roots = []
    for key in ("ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        v = os.environ.get(key)
        if v:
            roots.append(_norm(v))
    _PROG_ROOTS = tuple(roots)
    temps = []
    for key in ("TEMP", "TMP"):
        v = os.environ.get(key)
        if v:
            temps.append(_norm(v))
    _TEMP_ROOTS = tuple(temps)


def _norm(path: str) -> str:
    return os.path.abspath(path).replace("/", "\\").lower()


def classify_path(path: str) -> PathContext:
    """Определяет контекст пути — где находится файл."""
    _init_roots()
    p = _norm(path)

    # 1. System
    if _SYS_ROOT and (p == _SYS_ROOT or p.startswith(_SYS_ROOT + "\\")):
        return PathContext.SYSTEM

    # 2. Program Files / ProgramData
    for base in _PROG_ROOTS:
        if p == base or p.startswith(base + "\\"):
            # Игры могут стоять и в Program Files
            if _has_dir_marker(p, GAME_LIBRARY_DIRS):
                return PathContext.GAME_LIBRARY
            if _has_dir_marker(p, DEV_TOOL_DIRS):
                return PathContext.DEV_TOOL
            return PathContext.PROGRAM_FILES

    # 3. Игровые библиотеки (обычно вне Program Files: D:\Steam, D:\Games)
    if _has_dir_marker(p, GAME_LIBRARY_DIRS):
        return PathContext.GAME_LIBRARY

    # 4. Dev-инструменты
    if _has_dir_marker(p, DEV_TOOL_DIRS):
        return PathContext.DEV_TOOL

    # 5. Temp
    for base in _TEMP_ROOTS:
        if p == base or p.startswith(base + "\\"):
            return PathContext.TEMP
    if "\\windows\\temp\\" in p or "\\appdata\\local\\temp\\" in p:
        return PathContext.TEMP

    # 6. User data
    if _USER_ROOT and (p == _USER_ROOT or p.startswith(_USER_ROOT + "\\")):
        rel = p[len(_USER_ROOT) + 1 :] if p != _USER_ROOT else ""
        head = rel.split("\\", 1)[0] if rel else ""
        if head in ("pictures", "videos", "music"):
            return PathContext.USER_MEDIA
        if head in ("documents", "downloads", "desktop"):
            return PathContext.USER_DATA
        return PathContext.USER_DATA

    return PathContext.UNKNOWN


def _has_dir_marker(path_norm: str, markers: frozenset[str]) -> bool:
    """Точное совпадение ЛЮБОГО компонента пути с маркером."""
    parts = path_norm.split("\\")
    for part in parts:
        if part in markers:
            return True
    return False


# =====================================================================
# Классификация файла
# =====================================================================


@dataclass(frozen=True)
class FileJudgement:
    verdict: FileVerdict
    reason: str
    rule: str  # короткий код правила для UI / логов


def _ext(path: str) -> str:
    return os.path.splitext(path)[1].lower()


def judge_file(
    path: str, size: int = 0, context: PathContext | None = None
) -> FileJudgement:
    """Решает, можно ли вообще рассматривать файл как кандидата на удаление."""
    ctx = context if context is not None else classify_path(path)
    ext = _ext(path)
    low = path.lower()

    # ---- 1. Система: категорически нет ----
    if ctx == PathContext.SYSTEM:
        return FileJudgement(FileVerdict.PROTECTED, "Системный файл Windows", "sys")

    # ---- 2. Program Files ----
    if ctx == PathContext.PROGRAM_FILES:
        if ext in SYSTEM_CRITICAL_EXTS:
            return FileJudgement(
                FileVerdict.PROTECTED, "Компонент установленной программы", "prog-sys"
            )
        if ext in (".exe", ".dll", ".so", ".dylib"):
            return FileJudgement(
                FileVerdict.PROTECTED, "Исполняемый модуль программы", "prog-exe"
            )
        return FileJudgement(
            FileVerdict.CAUTION, "Файл внутри установленной программы", "prog"
        )

    # ---- 3. Игровые библиотеки ----
    if ctx == PathContext.GAME_LIBRARY:
        if ext in GAME_ASSET_EXTS:
            return FileJudgement(
                FileVerdict.PROTECTED,
                "Игровой ресурс — удаление сломает игру",
                "game-asset",
            )
        # Внутри игровых папок — только явные кэши/логи считаем CAUTION
        if any(k in low for k in _GAME_SAFE_SUBDIRS):
            return FileJudgement(
                FileVerdict.CAUTION, "Кэш или логи игрового лаунчера", "game-cache"
            )
        # Всё остальное в игровой библиотеке — тоже не трогаем
        return FileJudgement(
            FileVerdict.PROTECTED,
            "Данные игры в библиотеке Steam/Epic/GOG",
            "game-data",
        )

    # ---- 4. Среды разработки ----
    if ctx == PathContext.DEV_TOOL:
        if ext in (".dll", ".exe", ".so", ".dylib", ".pyd", ".node"):
            return FileJudgement(
                FileVerdict.PROTECTED,
                "Библиотека среды разработки — копия обязательна",
                "dev-lib",
            )
        if ext in (
            ".py",
            ".js",
            ".ts",
            ".tsx",
            ".jsx",
            ".json",
            ".md",
            ".c",
            ".cpp",
            ".h",
            ".hpp",
            ".cs",
            ".java",
            ".rs",
            ".go",
            ".kt",
            ".rb",
            ".php",
        ):
            return FileJudgement(
                FileVerdict.CAUTION, "Исходник среды разработки", "dev-src"
            )
        if any(
            k in low
            for k in (
                "\\__pycache__\\",
                "\\.pytest_cache\\",
                "\\pip\\cache\\",
                "\\node_modules\\.cache\\",
            )
        ):
            return FileJudgement(
                FileVerdict.CAUTION, "Кэш инструмента разработки", "dev-cache"
            )
        return FileJudgement(FileVerdict.CAUTION, "Файл внутри среды разработки", "dev")

    # ---- 5. Важные данные ----
    if ext in IMPORTANT_DATA_EXTS:
        return FileJudgement(
            FileVerdict.CAUTION,
            "База данных / проект / образ — проверьте вручную",
            "important-data",
        )

    # ---- 6. Temp: всё безопасно ----
    if ctx == PathContext.TEMP:
        return FileJudgement(FileVerdict.SAFE, "Временный файл", "temp")

    # ---- 7. Пользовательские данные ----
    if ctx in (PathContext.USER_DATA, PathContext.USER_MEDIA):
        return FileJudgement(
            FileVerdict.USER_DATA,
            "Личный файл пользователя — удаляйте осознанно",
            "user",
        )

    # ---- 8. Неизвестный контекст ----
    return FileJudgement(
        FileVerdict.CAUTION, "Неизвестный контекст — осторожно", "unknown"
    )


def is_protected(path: str, size: int = 0) -> bool:
    return judge_file(path, size).verdict == FileVerdict.PROTECTED


def should_auto_check(path: str, size: int = 0) -> bool:
    """True только для SAFE — где галочка ставится сразу."""
    return judge_file(path, size).verdict == FileVerdict.SAFE


# =====================================================================
# Проверка «пакетности» игровых ассетов
# =====================================================================

_ASSET_FAMILIES = (
    frozenset({".pak", ".ucas", ".utoc", ".sig"}),
    frozenset({".rpkg", ".pack", ".archive", ".bundle"}),
    frozenset({".bsa", ".ba2", ".esm", ".esp", ".esl"}),
    frozenset({".assets", ".resource", ".unity3d"}),
)


def looks_like_asset_group(path: str, siblings: list[str]) -> bool:
    """Если рядом лежит ≥2 файла того же игрового семейства — это игровой набор."""
    ext = _ext(path)
    for family in _ASSET_FAMILIES:
        if ext not in family:
            continue
        count = sum(1 for s in siblings if _ext(s) in family)
        if count >= 2:
            return True
    return False
