"""Tests for stamp_assets.py (deploy-time CSS/JS version tags).

Run from this folder:  python -m pytest -q
"""

from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

import pytest

import stamp_assets

HERE = Path(__file__).parent


def tag_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[: stamp_assets.TAG_LENGTH]


@pytest.fixture
def site(tmp_path: Path) -> Path:
    (tmp_path / "style.css").write_text("body { color: red; }\n")
    (tmp_path / "common.js").write_text("const PROXY_BASE_URL = '';\n")
    (tmp_path / "script.js").write_text("console.log('hi');\n")
    (tmp_path / "index.html").write_text(
        '<link rel="stylesheet" href="style.css">\n'
        '<script src="common.js"></script>\n'
        "<script src='script.js'></script>\n"
    )
    return tmp_path


def test_tags_local_css_and_js_with_content_hash(site: Path):
    stamp_assets.stamp_folder(site)
    html = (site / "index.html").read_text()
    assert f'href="style.css?v={tag_of((site / "style.css").read_bytes())}"' in html
    assert f'src="common.js?v={tag_of((site / "common.js").read_bytes())}"' in html
    # Single quotes are kept as single quotes.
    assert f"src='script.js?v={tag_of((site / 'script.js').read_bytes())}'" in html


def test_running_twice_changes_nothing(site: Path):
    stamp_assets.stamp_folder(site)
    first = (site / "index.html").read_text()
    report = stamp_assets.stamp_folder(site)
    assert report == {}
    assert (site / "index.html").read_text() == first
    assert first.count("?v=") == 3  # replaced, never stacked


def test_tag_changes_only_for_the_file_that_changed(site: Path):
    stamp_assets.stamp_folder(site)
    before = (site / "index.html").read_text()
    (site / "script.js").write_text("console.log('new release');\n")
    report = stamp_assets.stamp_folder(site)
    after = (site / "index.html").read_text()

    assert list(report) == ["index.html"]
    assert len(report["index.html"]) == 1 and report["index.html"][0].startswith("script.js ->")
    new_tag = tag_of((site / "script.js").read_bytes())
    assert f"script.js?v={new_tag}" in after
    # The other two links are untouched.
    for line in before.splitlines()[:2]:
        assert line in after


def test_leaves_external_absolute_and_missing_refs_alone(tmp_path: Path):
    (tmp_path / "app.js").write_text("x")
    page = (
        '<script src="https://cdn.example.com/lib.js"></script>\n'
        '<script src="//cdn.example.com/lib2.js"></script>\n'
        '<script src="/gallery/thing.js"></script>\n'
        '<link rel="stylesheet" href="missing.css">\n'
        '<a href="notes.txt">notes</a>\n'
        '<script src="app.js"></script>\n'
    )
    (tmp_path / "index.html").write_text(page)
    stamp_assets.stamp_folder(tmp_path)
    out = (tmp_path / "index.html").read_text()
    assert out.splitlines()[:5] == page.splitlines()[:5]
    assert f'src="app.js?v={tag_of(b"x")}"' in out


def test_does_not_follow_paths_outside_the_folder(tmp_path: Path):
    site = tmp_path / "site"
    site.mkdir()
    (tmp_path / "outside.js").write_text("secret")
    (site / "index.html").write_text('<script src="../outside.js"></script>\n')
    assert stamp_assets.stamp_folder(site) == {}
    assert "?v=" not in (site / "index.html").read_text()


def test_dry_run_reports_without_writing(site: Path):
    original = (site / "index.html").read_text()
    report = stamp_assets.stamp_folder(site, dry_run=True)
    assert len(report["index.html"]) == 3
    assert (site / "index.html").read_text() == original


def test_command_line(site: Path, capsys):
    assert stamp_assets.main([str(site)]) == 0
    assert "Updated index.html:" in capsys.readouterr().out
    assert stamp_assets.main([str(site)]) == 0
    assert "Nothing to change" in capsys.readouterr().out
    assert stamp_assets.main([str(site / "nope")]) == 1


def test_real_gallery_pages_are_fully_tagged(tmp_path: Path):
    # Stamp a copy of the actual frontend: every stylesheet/script on both
    # pages gets a tag, and nothing else in the markup changes.
    for name in ("index.html", "about.html", "style.css", "common.js", "script.js"):
        shutil.copy(HERE / name, tmp_path / name)
    stamp_assets.stamp_folder(tmp_path)
    for page, refs in {
        "index.html": ["style.css", "common.js", "script.js"],
        "about.html": ["style.css", "common.js"],
    }.items():
        out = (tmp_path / page).read_text()
        for ref in refs:
            tag = tag_of((HERE / ref).read_bytes())
            assert f'"{ref}?v={tag}"' in out, (page, ref)
        assert re.sub(r"\?v=[0-9a-f]+", "", out) == (HERE / page).read_text()
