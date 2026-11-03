"""Button colours stay consistent: filter = blue, add = green, import tolls / upcoming maintenance = yellow."""

import re
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parents[2] / "app" / "templates"
CSS = Path(__file__).resolve().parents[2] / "static" / "css"

ADD_LINKS = (
    "/vehicles/new",
    "/drivers/new",
    "/expenses/new",
    "/maintenance/new",
    "/compliance/new",
    "/company-licence/new",
    "/settings/users/new",
)
WARN_LINKS = ("/expenses/imports", "/maintenance/due")


def templates():
    return sorted(TEMPLATES.rglob("*.html"))


def tags(text, name):
    return re.findall(rf"<{name}\b[^>]*>", text)


def test_every_filter_button_uses_the_filter_class():
    found = 0
    for path in templates():
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"(<button\b[^>]*>)\s*\{\{ _\('btn\.filter'\) \}\}", text):
            found += 1
            assert "fc-btn-filter" in match.group(1), f"{path.name}: {match.group(1)}"
    assert found >= 6


@pytest.mark.parametrize("href", ADD_LINKS)
def test_add_links_are_green(href):
    anchors = [
        (path.name, tag)
        for path in templates()
        for tag in tags(path.read_text(encoding="utf-8"), "a")
        if f'href="{href}"' in tag and "fc-btn-" in tag or f'href="{href}"' in tag and "bg-primary" in tag
    ]
    assert anchors, href
    for name, tag in anchors:
        assert "fc-btn-add" in tag, f"{name}: {tag}"


@pytest.mark.parametrize("href", WARN_LINKS)
def test_import_tolls_and_upcoming_maintenance_are_yellow(href):
    anchors = [
        tag
        for path in templates()
        for tag in tags(path.read_text(encoding="utf-8"), "a")
        if f'href="{href}"' in tag and "h-10" in tag or f'href="{href}"' in tag and "fc-btn-" in tag
    ]
    assert anchors, href
    assert all("fc-btn-warn" in tag for tag in anchors), anchors


def test_compiled_css_has_the_three_button_colours():
    css = (CSS / "app.css").read_text(encoding="utf-8")
    for name, colour in (("fc-btn-add", "240 253 244"), ("fc-btn-warn", "254 252 232"), ("fc-btn-filter", "37 99 235")):
        match = re.search(rf"\.{name}\{{[^}}]*\}}", css)
        assert match and colour in match.group(0), name
