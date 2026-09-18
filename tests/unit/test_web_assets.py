"""The shipped UI must satisfy the Content-Security-Policy it is served under.

`style-src 'self'` with no 'unsafe-inline' means a `style="..."` attribute is
refused by the browser and the element renders unstyled — a failure that is
invisible to any test that does not run a real browser. These checks are the
cheap substitute.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / "src" / "insight_engine" / "web"

HTML_PAGES = sorted(WEB.glob("*.html"))
SCRIPTS = sorted((WEB / "assets").glob("*.js"))

INLINE_STYLE_ATTR = re.compile(r"""\sstyle\s*=\s*["']""")
INLINE_EVENT_ATTR = re.compile(r"""\son(?:click|load|error|change|submit)\s*=\s*["']""")
STYLE_STRING_KEY = re.compile(r"""style:\s*["'`]""")

BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
LINE_COMMENT = re.compile(r"^\s*//.*$", re.MULTILINE)


def code_of(script: Path) -> str:
    """Script source with comments removed.

    These checks are about what the code does, and the comments explaining why
    it does not use innerHTML would otherwise trip the check for innerHTML.
    """
    text = script.read_text(encoding="utf-8")
    return LINE_COMMENT.sub("", BLOCK_COMMENT.sub("", text))


def test_the_web_root_ships_the_expected_files() -> None:
    assert {p.name for p in HTML_PAGES} == {"index.html", "dashboard.html"}
    assert {p.name for p in SCRIPTS} == {"app.js", "dashboard.js", "ui.js"}
    assert (WEB / "assets" / "app.css").is_file()
    assert (WEB / "assets" / "favicon.svg").is_file()


@pytest.mark.parametrize("page", HTML_PAGES, ids=lambda p: p.name)
class TestHtml:
    def test_no_inline_style_attributes(self, page: Path) -> None:
        offenders = INLINE_STYLE_ATTR.findall(page.read_text(encoding="utf-8"))
        assert not offenders, f"{page.name} uses {len(offenders)} style attribute(s)"

    def test_no_inline_event_handlers(self, page: Path) -> None:
        assert not INLINE_EVENT_ATTR.search(page.read_text(encoding="utf-8"))

    def test_no_inline_script_or_style_blocks(self, page: Path) -> None:
        text = page.read_text(encoding="utf-8")
        assert "<style" not in text
        assert not re.search(r"<script(?![^>]*\ssrc=)", text)

    def test_loads_nothing_from_a_third_party_origin(self, page: Path) -> None:
        text = page.read_text(encoding="utf-8")
        for match in re.findall(r'(?:src|href)="([^"]+)"', text):
            assert not match.startswith(("http://", "//")), match
            if match.startswith("https://"):
                # Only outbound links in prose, never a loaded subresource.
                assert "github.com" in match, match

    def test_declares_a_language_and_a_viewport(self, page: Path) -> None:
        text = page.read_text(encoding="utf-8")
        assert '<html lang="en"' in text
        assert 'name="viewport"' in text

    def test_has_a_skip_link_and_a_main_landmark(self, page: Path) -> None:
        text = page.read_text(encoding="utf-8")
        assert 'class="skip-link"' in text
        assert 'id="main"' in text


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
class TestScripts:
    def test_no_style_attribute_strings(self, script: Path) -> None:
        assert not STYLE_STRING_KEY.search(code_of(script))

    def test_nothing_is_rendered_with_innerhtml(self, script: Path) -> None:
        # Every value rendered can originate from a CSV header, a segment value
        # or model-generated prose.
        code = code_of(script)
        assert "innerHTML" not in code
        assert "outerHTML" not in code
        assert "insertAdjacentHTML" not in code

    def test_no_eval_or_dynamic_function_construction(self, script: Path) -> None:
        code = code_of(script)
        assert not re.search(r"\beval\s*\(", code)
        assert not re.search(r"\bnew\s+Function\s*\(", code)


def test_the_stylesheet_defines_both_themes() -> None:
    css = (WEB / "assets" / "app.css").read_text(encoding="utf-8")
    assert "prefers-color-scheme: dark" in css
    assert ':root[data-theme="dark"]' in css
    assert "prefers-reduced-motion: reduce" in css
    # A focus ring on every interactive element; the previous build set
    # `outline: none` with no replacement, which made the form unnavigable.
    assert ":focus-visible" in css
    assert "--focus-ring" in css
