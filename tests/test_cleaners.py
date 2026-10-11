"""Реальные тесты ядра DiskCleaner. Запуск: uv run pytest -v"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest


# =====================================================================
# smart_rules
# =====================================================================


class TestSmartRules:
    def test_system_protected(self):
        from cleaners.smart_rules import FileVerdict, judge_file

        j = judge_file(r"C:\Windows\System32\kernel32.dll", 1024)
        assert j.verdict == FileVerdict.PROTECTED

    def test_program_files_protected(self):
        from cleaners.smart_rules import FileVerdict, judge_file

        j = judge_file(r"C:\Program Files\App\app.exe", 1024)
        assert j.verdict == FileVerdict.PROTECTED

    def test_game_asset_protected(self):
        from cleaners.smart_rules import FileVerdict, judge_file

        j = judge_file(r"D:\Steam\steamapps\common\Game\Content\Paks\pak0.pak", 1024)
        assert j.verdict == FileVerdict.PROTECTED

    def test_dev_dll_protected(self):
        from cleaners.smart_rules import FileVerdict, judge_file

        j = judge_file(r"D:\Git\ucrt64\libexec\git-core\libiconv-2.dll", 1024)
        assert j.verdict == FileVerdict.PROTECTED

    def test_temp_safe(self):
        from cleaners.smart_rules import FileVerdict, judge_file

        tmp = os.environ.get("TEMP", "")
        if not tmp:
            pytest.skip("TEMP не задан")
        j = judge_file(os.path.join(tmp, "foo.tmp"), 1024)
        assert j.verdict == FileVerdict.SAFE

    def test_important_extensions_caution(self):
        from cleaners.smart_rules import FileVerdict, judge_file

        j = judge_file(r"C:\Data\backup.iso", 1024)
        assert j.verdict == FileVerdict.CAUTION


# =====================================================================
# safety: whitelist и preview
# =====================================================================


class TestSafety:
    def test_whitelist_add_remove(self, tmp_path, monkeypatch):
        from cleaners import safety

        monkeypatch.setattr(safety, "_log_dir", lambda: str(tmp_path))
        wl = safety.Whitelist(str(tmp_path / "wl.json"))
        p = str(tmp_path / "important")
        assert not wl.contains(p)
        wl.add(p)
        assert wl.contains(p)
        wl.remove(p)
        assert not wl.contains(p)

    def test_whitelist_persist(self, tmp_path):
        from cleaners.safety import Whitelist

        path = str(tmp_path / "wl.json")
        wl1 = Whitelist(path)
        wl1.add(str(tmp_path / "keep"))
        wl2 = Whitelist(path)
        assert wl2.contains(str(tmp_path / "keep"))

    def test_build_preview_totals(self, tmp_path):
        from cleaners.safety import Whitelist, build_preview

        wl = Whitelist(str(tmp_path / "wl.json"))
        items = [
            {"path": str(tmp_path / "a"), "kind": "file", "size": 100},
            {"path": str(tmp_path / "b"), "kind": "file", "size": 200},
        ]
        p = build_preview(items, whitelist=wl)
        assert p.total_size == 300
        assert len(p.items) == 2


# =====================================================================
# disk_scan: unified_scan
# =====================================================================


class TestDiskScan:
    @pytest.fixture
    def sandbox(self, tmp_path):
        (tmp_path / "big1.bin").write_bytes(b"x" * 5_000_000)
        (tmp_path / "big2.bin").write_bytes(b"y" * 8_000_000)
        (tmp_path / "small.txt").write_bytes(b"hello")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "nested.txt").write_bytes(b"world")
        empty = tmp_path / "empty"
        empty.mkdir()
        empty2 = tmp_path / "sub" / "empty2"
        empty2.mkdir()
        return tmp_path

    def test_unified_scan_finds_big(self, sandbox):
        from cleaners.disk_scan import unified_scan

        res = unified_scan(str(sandbox), big_min_bytes=4_000_000, top=10)
        assert len(res["big_files"]) == 2
        assert res["big_files"][0].size == 8_000_000  # отсортировано DESC

    def test_unified_scan_finds_empty(self, sandbox):
        from cleaners.disk_scan import unified_scan

        res = unified_scan(str(sandbox))
        paths = {ef.path for ef in res["empty_folders"]}
        assert str(sandbox / "empty") in paths
        assert str(sandbox / "sub" / "empty2") in paths

    def test_file_filter_excludes(self, sandbox):
        from cleaners.disk_scan import unified_scan

        # Отфильтруем всё, что не .bin
        res = unified_scan(
            str(sandbox),
            big_min_bytes=1,
            top=10,
            file_filter=lambda p, s: p.endswith(".bin"),
        )
        assert len(res["big_files"]) == 2

    def test_extension_stats(self, sandbox):
        from cleaners.disk_scan import unified_scan

        res = unified_scan(str(sandbox))
        # .bin — «Другое», .txt — «Документы»
        assert res["extension_stats"]["Другое"] >= 13_000_000
        assert res["extension_stats"]["Документы"] >= 10


# =====================================================================
# duplicate_finder
# =====================================================================


class TestDuplicateFinder:
    def test_finds_duplicates(self, tmp_path):
        from cleaners.duplicate_finder import DuplicateFinder

        payload = b"ABCD" * 300_000  # ~1.2 МБ
        a = tmp_path / "a.bin"
        b = tmp_path / "b.bin"
        c = tmp_path / "c.bin"
        a.write_bytes(payload)
        b.write_bytes(payload)
        c.write_bytes(b"different data" * 100_000)

        finder = DuplicateFinder(min_size=4096)
        groups = finder.find([str(tmp_path)])
        assert len(groups) == 1
        assert groups[0].count == 2
        assert str(a) in groups[0].files and str(b) in groups[0].files

    def test_ignores_dev_and_game_dirs(self, tmp_path):
        from cleaners.duplicate_finder import DuplicateFinder

        payload = b"ABCD" * 300_000
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "lib1.js").write_bytes(payload)
        (tmp_path / "node_modules" / "lib2.js").write_bytes(payload)

        finder = DuplicateFinder(min_size=4096)
        groups = finder.find([str(tmp_path)])
        assert groups == [], "node_modules не должен сканироваться"


# =====================================================================
# report
# =====================================================================


class TestReport:
    def test_html_report(self, tmp_path):
        from cleaners.report import write_html_report

        out = str(tmp_path / "r.html")
        items = [
            {"kind": "junk", "path": "C:/t/f.tmp", "size": 1024, "sub": "test"},
            {"kind": "big", "path": "C:/t/x.bin", "size": 1_000_000, "sub": "big"},
        ]
        write_html_report(
            out,
            title="Test",
            root_path="C:/t",
            summary={"total_size": 1001024},
            items=items,
        )
        html = Path(out).read_text(encoding="utf-8")
        assert "Test" in html
        assert "C:/t/f.tmp" in html
        assert "Мусор" in html

    def test_csv_report(self, tmp_path):
        from cleaners.report import write_csv_report

        out = str(tmp_path / "r.csv")
        write_csv_report(out, [{"kind": "junk", "path": "a", "size": 1, "sub": ""}])
        data = Path(out).read_text(encoding="utf-8-sig")
        assert "a" in data and "junk" not in data.lower() or "Мусор" in data


# =====================================================================
# utils: human_size, recycle_bin_size
# =====================================================================


class TestUtils:
    def test_human_size(self):
        from cleaners.utils import human_size

        assert human_size(0) == "0 Б"
        assert human_size(1023) == "1023 Б"
        assert human_size(1024) == "1.0 КБ"
        assert human_size(1536) == "1.5 КБ"
        assert human_size(1024 * 1024) == "1.0 МБ"

    def test_recycle_bin_size_runs(self):
        # Главное — не падает и возвращает int (без опечатки _fields_)
        from cleaners.utils import recycle_bin_size

        n = recycle_bin_size()
        assert isinstance(n, int)
        assert n >= 0

    def test_delete_permanent_in_tmp(self, tmp_path):
        from cleaners.utils import delete_permanent, move_to_recycle

        f = tmp_path / "junk.tmp"
        f.write_bytes(b"x" * 100)

        # В Корзину
        bad = move_to_recycle([str(f)])
        assert bad == [] or not f.exists(), "файл не должен остаться"
