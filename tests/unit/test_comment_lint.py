"""Unit tests for `tools.comment_lint`.

Covers the allowlist (is_preserved), comment location (find_disallowed),
removal (strip_comments / fix_file) including the AST-equality guard, path
discovery with excludes, and the CLI (main) exit-code contract. Real files on
disk under tmp_path are used throughout; the ruff formatter is injected as a
fake for the unit cases and exercised for real in the format-check guarantee.
"""

from __future__ import annotations

from pathlib import Path

import comment_lint as cl
import pytest


def _identity_formatter(source: str, path: Path) -> str:
    return source


@pytest.mark.unit
class TestIsPreserved:
    @pytest.mark.parametrize(
        ("text", "row", "col", "expected"),
        [
            ("#!/usr/bin/env python3", 1, 0, True),
            ("#!/usr/bin/env python3", 5, 0, False),
            ("# -*- coding: utf-8 -*-", 1, 0, True),
            ("# -*- coding: utf-8 -*-", 2, 0, True),
            ("# -*- coding: utf-8 -*-", 7, 0, False),
            ("# type: ignore[attr-defined]", 12, 20, True),
            ("#type: ignore", 3, 4, True),
            ("# pragma: no cover", 9, 0, True),
            ("# pragma: no cover - defensive", 9, 4, True),
            ("# noqa: E402", 476, 30, True),
            ("# nosec B101", 4, 10, True),
            ("# ruff: noqa", 1, 0, True),
            ("# fmt: off", 5, 4, True),
            ("# just an explanatory comment", 4, 0, False),
            ("# TODO: refactor later", 4, 0, False),
        ],
    )
    def test_allowlist(self, text: str, row: int, col: int, expected: bool) -> None:
        assert cl.is_preserved(text, row, col) is expected


@pytest.mark.unit
class TestFindDisallowed:
    def test_flags_plain_comment(self, tmp_path: Path) -> None:
        path = tmp_path / "m.py"
        violations = cl.find_disallowed(path, "x = 1  # explain\n")
        assert len(violations) == 1
        assert violations[0].row == 1
        assert violations[0].text == "# explain"

    def test_docstring_not_flagged(self, tmp_path: Path) -> None:
        source = '"""Module docstring with a # hash inside it."""\nx = 1\n'
        assert cl.find_disallowed(tmp_path / "m.py", source) == []

    def test_hash_inside_string_literal_not_flagged(self, tmp_path: Path) -> None:
        source = (
            "BANNED = (\n"
            '    "Never add # noqa, # nosec, "\n'
            '    "# type: ignore, or # pragma: no cover.\\n"\n'
            ")\n"
            'URL = "http://example.com/#frag"\n'
        )
        assert cl.find_disallowed(tmp_path / "m.py", source) == []

    def test_directives_not_flagged(self, tmp_path: Path) -> None:
        source = "import os  # type: ignore[import-not-found]\nimport sys  # noqa: E402\ny = 1  # pragma: no cover\n"
        assert cl.find_disallowed(tmp_path / "m.py", source) == []

    def test_unparseable_is_fatal(self, tmp_path: Path) -> None:
        with pytest.raises(cl.CommentLintError):
            cl.find_disallowed(tmp_path / "m.py", "def broken(:\n")


@pytest.mark.unit
class TestStripComments:
    def test_drops_comment_only_line(self) -> None:
        source = "a = 1\n# a comment line\nb = 2\n"
        result = cl.strip_comments(source, {2: 0})
        assert result == "a = 1\nb = 2\n"

    def test_strips_trailing_comment_and_rstrips(self) -> None:
        source = "a = 1   # trailing\n"
        result = cl.strip_comments(source, {1: 8})
        assert result == "a = 1\n"

    def test_indented_comment_only_line_dropped(self) -> None:
        source = "def f():\n    # body comment\n    return 1\n"
        result = cl.strip_comments(source, {2: 4})
        assert result == "def f():\n    return 1\n"


@pytest.mark.unit
class TestFixFile:
    def test_removes_comment_preserves_docstring_and_ast(self, tmp_path: Path) -> None:
        source = '"""Doc."""\nx = 1  # explain\n\n\ndef f() -> int:\n    return x\n'
        result = cl.fix_file(tmp_path / "m.py", source, _identity_formatter)
        assert result is not None
        assert "# explain" not in result
        assert '"""Doc."""' in result
        before = cl._ast_dump(source, tmp_path / "m.py")
        after = cl._ast_dump(result, tmp_path / "m.py")
        assert before == after

    def test_preserves_directives_and_shebang(self, tmp_path: Path) -> None:
        source = "#!/usr/bin/env python3\nimport os  # type: ignore[x]\ny = 1  # pragma: no cover\nz = 2  # drop me\n"
        result = cl.fix_file(tmp_path / "m.py", source, _identity_formatter)
        assert result is not None
        assert result.startswith("#!/usr/bin/env python3\n")
        assert "# type: ignore[x]" in result
        assert "# pragma: no cover" in result
        assert "# drop me" not in result

    def test_comment_inside_open_bracket(self, tmp_path: Path) -> None:
        source = "vals = [\n    1,  # one\n    2,\n]\n"
        result = cl.fix_file(tmp_path / "m.py", source, _identity_formatter)
        assert result is not None
        assert "# one" not in result
        assert cl._ast_dump(source, tmp_path / "m.py") == cl._ast_dump(result, tmp_path / "m.py")

    def test_returns_none_when_no_comments(self, tmp_path: Path) -> None:
        assert cl.fix_file(tmp_path / "m.py", "x = 1\n", _identity_formatter) is None

    def test_ast_guard_aborts_on_corrupting_formatter(self, tmp_path: Path) -> None:
        def corrupting(source: str, path: Path) -> str:
            return "y = 999\n"

        with pytest.raises(cl.CommentLintError):
            cl.fix_file(tmp_path / "m.py", "x = 1  # c\n", corrupting)


@pytest.mark.unit
class TestDiscover:
    def test_excludes_glob_and_skips_pycache(self, tmp_path: Path) -> None:
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "a.py").write_text("a = 1\n")
        (tmp_path / "pkg" / "__pycache__").mkdir()
        (tmp_path / "pkg" / "__pycache__" / "a.cpython-312.py").write_text("a = 1\n")
        (tmp_path / "fixtures").mkdir()
        (tmp_path / "fixtures" / "b.py").write_text("b = 1\n")
        found = cl.discover([str(tmp_path / "pkg"), str(tmp_path / "fixtures")], [f"{tmp_path.as_posix()}/fixtures/**"])
        names = {p.name for p in found}
        assert names == {"a.py"}

    def test_missing_path_is_fatal(self, tmp_path: Path) -> None:
        with pytest.raises(cl.CommentLintError):
            cl.discover([str(tmp_path / "nope")], [])


@pytest.mark.unit
class TestMain:
    def test_check_clean_returns_zero(self, tmp_path: Path) -> None:
        (tmp_path / "clean.py").write_text('"""Doc."""\nx = 1\n')
        assert cl.main(["--check", str(tmp_path / "clean.py")]) == cl.EXIT_OK

    def test_check_dirty_returns_one(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        (tmp_path / "dirty.py").write_text("x = 1  # explain\n")
        assert cl.main(["--check", str(tmp_path / "dirty.py")]) == cl.EXIT_FOUND
        out = capsys.readouterr()
        assert "disallowed comment" in out.out

    def test_check_is_default_mode(self, tmp_path: Path) -> None:
        (tmp_path / "dirty.py").write_text("x = 1  # explain\n")
        assert cl.main([str(tmp_path / "dirty.py")]) == cl.EXIT_FOUND

    def test_unparseable_returns_fatal(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        (tmp_path / "bad.py").write_text("def broken(:\n")
        assert cl.main(["--check", str(tmp_path / "bad.py")]) == cl.EXIT_FATAL
        assert "ERROR:" in capsys.readouterr().err

    def test_fix_writes_and_is_idempotent(self, tmp_path: Path) -> None:
        target = tmp_path / "m.py"
        target.write_text("x = 1  # explain\n")
        assert cl.main(["--fix", str(target)], formatter=_identity_formatter) == cl.EXIT_OK
        assert "# explain" not in target.read_text()
        assert cl.main(["--check", str(target)]) == cl.EXIT_OK


@pytest.mark.unit
class TestRuffIntegration:
    def test_fix_output_is_format_and_import_clean(self, tmp_path: Path) -> None:
        import subprocess

        target = tmp_path / "m.py"
        target.write_text(
            '"""Doc."""\n\n'
            "from __future__ import annotations\n\n"
            "import os\n\n\n"
            "# a comment-only line after imports\n"
            "def f() -> str:\n"
            "    # body comment\n"
            "    return os.getcwd()  # trailing\n"
        )
        assert cl.main(["--fix", str(target)]) == cl.EXIT_OK
        fmt = subprocess.run(["ruff", "format", "--check", str(target)], capture_output=True, text=True, check=False)
        assert fmt.returncode == 0, fmt.stdout + fmt.stderr
        imp = subprocess.run(
            ["ruff", "check", "--select", "I", str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert imp.returncode == 0, imp.stdout + imp.stderr
        body = target.read_text()
        assert "# a comment-only line" not in body
        assert "# body comment" not in body
        assert "# trailing" not in body
