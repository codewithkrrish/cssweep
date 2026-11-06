"""Tests for cssweep — happy path and the awkward cases."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from cssweep.analyze import analyze
from cssweep.cleaner import clean_text
from cssweep.cli import run
from cssweep.config import load_config
from cssweep.cssparse import iter_style_rules, parse_css
from cssweep.errors import ConfigError, CssParseError
from cssweep.scanner import find_files
from cssweep.selectors import custom_props_defined, selector_names
from cssweep.usage import collect_usage

CSS = """
/* a comment with a { brace in it */
.btn { color: var(--accent); }
.card, .unused-card { padding: 1rem; }
#main-nav { display: flex; }
a:hover { text-decoration: none; }
@media (max-width: 600px) {
  .btn-small { font-size: 12px; }
  .never-small { font-size: 10px; }
}
@keyframes fade { from { opacity: 0; } to { opacity: 1; } }
:root { --accent: #f00; --dead-token: #00f; }
"""

HTML = """
<html><body>
  <nav id="main-nav"><button class="btn btn-small">Go</button></nav>
  <div class="card"></div>
  <style>.inline-rule { color: blue; }</style>
</body></html>
"""


def _scan(tmp_path: Path, css_text: str = CSS, html_text: str = HTML):
    css_path = tmp_path / "styles.css"
    css_path.write_text(css_text)
    html_path = tmp_path / "index.html"
    html_path.write_text(html_text)
    css_files, template_files = find_files(tmp_path)
    usage = collect_usage(template_files, css_files)
    texts = {p: p.read_text() for p in css_files}
    result = analyze(tmp_path, texts, usage, [], skip_ids=False)
    result.template_files = len(template_files)
    return result, css_path, html_path


# --- parsing ------------------------------------------------------------


def test_parse_counts_rules_and_skips_keyframes():
    rules = iter_style_rules(parse_css(CSS))
    selectors = [s for r in rules for s in r.selectors]
    assert ".btn" in selectors
    assert ".card" in selectors
    assert ".unused-card" in selectors
    assert "#main-nav" in selectors
    assert ".btn-small" in selectors
    assert ".never-small" in selectors
    assert ":root" in selectors
    # Keyframe steps are not selectors.
    assert "from" not in selectors and "to" not in selectors
    assert len(rules) == 7


def test_parse_media_context_recorded():
    rules = iter_style_rules(parse_css(CSS))
    small = next(r for r in rules if ".btn-small" in r.selectors)
    assert small.at_context == ("@media (max-width: 600px)",)


def test_parse_unbalanced_brace_is_an_error_not_a_traceback():
    with pytest.raises(CssParseError, match="unbalanced"):
        parse_css(".btn { color: red; ")


def test_parse_brace_inside_string_does_not_end_rule():
    rules = iter_style_rules(parse_css('.x::after { content: "}"; }\n.y { color: red; }'))
    assert [r.selectors for r in rules] == [[".x::after"], [".y"]]


def test_parse_empty_css():
    assert parse_css("") == []
    assert parse_css("/* only a comment */") == []


# --- selector extraction -------------------------------------------------


def test_selector_names_pseudo_and_combinators():
    classes, ids = selector_names(".card.featured > #main a:hover::before")
    assert classes == {"card", "featured"}
    assert ids == {"main"}


def test_selector_names_attribute_values_are_not_classes():
    classes, _ = selector_names('[data-state="open"] .btn')
    assert classes == {"btn"}


def test_custom_props_defined_and_used():
    assert custom_props_defined(":root { --accent: red; --gap: 8px; }") == {
        "--accent",
        "--gap",
    }


# --- analysis ------------------------------------------------------------


def test_scan_finds_the_dead_ones(tmp_path):
    result, _, _ = _scan(tmp_path)
    unused = {u.name for u in sum((f.unused for f in result.files), [])}
    assert unused == {".unused-card", ".never-small", "--dead-token"}


def test_scan_does_not_flag_used_selectors(tmp_path):
    result, _, _ = _scan(tmp_path)
    unused = {u.name for u in sum((f.unused for f in result.files), [])}
    assert ".btn" not in unused
    assert ".card" not in unused
    assert "#main-nav" not in unused
    assert ".btn-small" not in unused
    assert "--accent" not in unused


def test_scan_element_only_rules_never_flagged(tmp_path):
    result, _, _ = _scan(tmp_path)
    unused = {u.name for u in sum((f.unused for f in result.files), [])}
    assert not any(u.startswith("a") for u in unused)


def test_scan_skip_ids(tmp_path):
    css_path = tmp_path / "s.css"
    css_path.write_text("#ghost { color: red; } .ghost { color: blue; }")
    (tmp_path / "i.html").write_text("<p>hi</p>")
    css_files, template_files = find_files(tmp_path)
    usage = collect_usage(template_files, css_files)
    result = analyze(
        tmp_path, {p: p.read_text() for p in css_files}, usage, [], skip_ids=True
    )
    names = {u.name for f in result.files for u in f.unused}
    assert names == {".ghost"}


def test_scan_ignore_pattern(tmp_path):
    css_path = tmp_path / "s.css"
    css_path.write_text(".js-modal { display: none; } .dead { color: red; }")
    (tmp_path / "i.html").write_text("<p>hi</p>")
    css_files, template_files = find_files(tmp_path)
    usage = collect_usage(template_files, css_files)
    result = analyze(
        tmp_path,
        {p: p.read_text() for p in css_files},
        usage,
        [re.compile(r"^js-")],
        skip_ids=False,
    )
    names = {u.name for f in result.files for u in f.unused}
    assert names == {".dead"}


def test_scan_duplicate_selectors(tmp_path):
    result, _, _ = _scan(
        tmp_path,
        css_text=".btn { color: red; }\n.btn { color: blue; }",
        html_text="<button class='btn'>x</button>",
    )
    assert len(result.duplicates) == 1
    assert result.duplicates[0].selector == ".btn"
    assert len(result.duplicates[0].locations) == 2


def test_scan_empty_project(tmp_path):
    css_files, template_files = find_files(tmp_path)
    usage = collect_usage(template_files, css_files)
    result = analyze(tmp_path, {}, usage, [], skip_ids=False)
    assert result.total_unused == 0
    assert result.total_rules == 0


def test_scan_skips_node_modules(tmp_path):
    (tmp_path / "node_modules" / "lib").mkdir(parents=True)
    (tmp_path / "node_modules" / "lib" / "x.css").write_text(".dead { color: red; }")
    (tmp_path / "app.css").write_text(".alive { color: green; }")
    (tmp_path / "index.html").write_text('<div class="alive"></div>')
    css_files, _ = find_files(tmp_path)
    assert css_files == [tmp_path / "app.css"]


def test_usage_from_js_classlist_and_vue(tmp_path):
    js = tmp_path / "app.js"
    js.write_text("el.classList.add('is-open');\nconst c = `card-${size}`;")
    vue = tmp_path / "Card.vue"
    vue.write_text('<div :class="{ highlighted: active }"></div>')
    usage = collect_usage([js, vue], [])
    assert "is-open" in usage.names
    assert "highlighted" in usage.names


# --- cleaning ------------------------------------------------------------


def test_clean_removes_dead_rule_and_trims_mixed_rule(tmp_path):
    result, css_path, _ = _scan(tmp_path)
    report = result.files[0]
    cleaned, stats = clean_text(css_path.read_text(), report)
    assert ".unused-card" not in cleaned
    assert ".card" in cleaned  # the mixed rule keeps its used selector
    assert ".never-small" not in cleaned
    assert ".btn-small" in cleaned  # media block survives, partly
    assert "--dead-token" not in cleaned
    assert "--accent" in cleaned
    assert stats.removed_rules >= 1
    assert stats.trimmed_rules == 1
    # Kept rules come out byte-identical.
    assert ".btn { color: var(--accent); }" in cleaned
    assert "#main-nav { display: flex; }" in cleaned


def test_clean_drops_empty_media_block(tmp_path):
    result, css_path, _ = _scan(
        tmp_path,
        css_text="@media (max-width: 600px) {\n  .gone { color: red; }\n}\n.kept { color: blue; }",
        html_text='<div class="kept"></div>',
    )
    cleaned, stats = clean_text(css_path.read_text(), result.files[0])
    assert "@media" not in cleaned
    assert ".kept" in cleaned
    assert stats.removed_rules == 1


def test_clean_leaves_everything_when_nothing_unused(tmp_path):
    result, css_path, _ = _scan(
        tmp_path,
        css_text=".btn { color: red; }",
        html_text='<button class="btn">x</button>',
    )
    cleaned, stats = clean_text(css_path.read_text(), result.files[0])
    assert cleaned == css_path.read_text()
    assert stats.bytes_saved == 0


# --- config --------------------------------------------------------------


def test_load_config_rejects_bad_regex(tmp_path):
    bad = tmp_path / ".cssweep.json"
    bad.write_text('{"ignore": ["("]}')
    with pytest.raises(ConfigError, match="bad ignore regex"):
        load_config(bad)


def test_load_config_rejects_unknown_keys(tmp_path):
    bad = tmp_path / ".cssweep.json"
    bad.write_text('{"surprise": true}')
    with pytest.raises(ConfigError, match="unknown option"):
        load_config(bad)


# --- CLI -----------------------------------------------------------------


def _run_cli(tmp_path: Path, *args: str) -> tuple[int, str, str]:
    import io
    from contextlib import redirect_stderr, redirect_stdout

    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = run([str(tmp_path), *args])
    return code, out.getvalue(), err.getvalue()


def test_cli_reports_unused_and_exits_zero_by_default(tmp_path):
    _scan(tmp_path)
    code, out, _ = _run_cli(tmp_path)
    assert code == 0
    assert ".unused-card" in out
    assert "3 unused selectors" in out


def test_cli_fail_on_unused(tmp_path):
    _scan(tmp_path)
    code, _, _ = _run_cli(tmp_path, "--fail-on-unused")
    assert code == 1


def test_cli_clean_dry_run_writes_nothing(tmp_path):
    _, css_path, _ = _scan(tmp_path)
    before = css_path.read_text()
    code, out, _ = _run_cli(tmp_path, "--clean", "--dry-run")
    assert code == 0
    assert "would clean" in out
    assert css_path.read_text() == before
    assert not list(tmp_path.glob("*.swept.css"))


def test_cli_clean_writes_swept_copy_by_default(tmp_path):
    _, css_path, _ = _scan(tmp_path)
    code, out, _ = _run_cli(tmp_path, "--clean")
    assert code == 0
    swept = tmp_path / "styles.swept.css"
    assert swept.exists()
    assert ".unused-card" not in swept.read_text()
    assert ".btn" in swept.read_text()
    # Original untouched.
    assert ".unused-card" in css_path.read_text()


def test_cli_clean_force_rewrites_in_place(tmp_path):
    _, css_path, _ = _scan(tmp_path)
    code, _, _ = _run_cli(tmp_path, "--clean", "--force")
    assert code == 0
    assert ".unused-card" not in css_path.read_text()
    assert not list(tmp_path.glob("*.swept.css"))


def test_cli_clean_never_overwrites_existing_swept_file(tmp_path):
    _, css_path, _ = _scan(tmp_path)
    existing = tmp_path / "styles.swept.css"
    existing.write_text("do not touch me")
    code, _, _ = _run_cli(tmp_path, "--clean")
    assert code == 0
    assert existing.read_text() == "do not touch me"
    assert (tmp_path / "styles.swept-2.css").exists()


def test_cli_missing_directory_is_a_clean_error(tmp_path):
    code, _, err = _run_cli(tmp_path / "nope")
    assert code == 2
    assert "no such file or directory" in err
    assert "Traceback" not in err


def test_cli_broken_css_is_a_clean_error(tmp_path):
    (tmp_path / "bad.css").write_text(".btn { color: red; ")
    (tmp_path / "i.html").write_text("<p>x</p>")
    code, _, err = _run_cli(tmp_path)
    assert code == 2
    assert "unbalanced" in err
    assert "Traceback" not in err


def test_cli_json_output_parses(tmp_path):
    _scan(tmp_path)
    code, out, _ = _run_cli(tmp_path, "--format", "json")
    assert code == 0
    payload = json.loads(out)
    assert payload["total_unused"] == 3
    assert payload["files"][0]["file"] == "styles.css"


def test_cli_no_stylesheets_is_not_an_error(tmp_path):
    (tmp_path / "index.html").write_text("<p>hi</p>")
    code, out, _ = _run_cli(tmp_path)
    assert code == 0
    assert "no stylesheets" in out


def test_cli_runs_as_module(tmp_path):
    _scan(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-m", "cssweep", str(tmp_path)],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parent.parent,
    )
    assert proc.returncode == 0
    assert ".unused-card" in proc.stdout
