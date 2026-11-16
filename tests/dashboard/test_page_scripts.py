"""Page scripts handle events whose target is not an element.

A capturing mouseenter or mouseleave listener on the document also fires for
the document itself, which has no closest(). The handlers must not throw.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import cost_display
import pytest
import views_base

_LAMBDA = Path(__file__).resolve().parents[2] / "modules" / "aws" / "compliance-dashboard" / "lambda"

# A document with listeners, an element class, and one element of each kind the scripts look for.
_HARNESS = r"""
class Element {
  constructor(cls) { this.cls = cls; this.classList = { add() {}, remove() {}, toggle() {} }; this.style = {}; }
  closest(sel) { return sel === '.' + this.cls ? this : null; }
  getAttribute() { return 'tip'; }
  getBoundingClientRect() { return { top: 100, bottom: 120, left: 10 }; }
  get parentElement() { return new Element('parent'); }
}
const listeners = {};
const documentElement = new Element('root');
const document = {
  documentElement,
  body: { appendChild() {} },
  createElement() { return new Element('hint-tip'); },
  addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
  querySelectorAll() { return []; },
};
const window = { innerWidth: 1200 };
globalThis.Element = Element; globalThis.document = document; globalThis.window = window;
const SCRIPT = %s;
eval(SCRIPT);
const targets = [document, { nodeType: 3 }, new Element('hint'), new Element('nav-group-label'), new Element('other')];
let calls = 0;
for (const type of Object.keys(listeners)) {
  for (const fn of listeners[type]) {
    for (const target of targets) { fn({ target }); calls += 1; }
  }
}
console.log(calls);
"""


def _js(script_html):
    """The JavaScript inside one <script>...</script> block."""
    text = script_html.strip()
    opening, closing = "<script>", "</script>"
    assert text.startswith(opening) and text.endswith(closing)
    return text[len(opening) : -len(closing)]


def _run(script_html):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    code = _HARNESS % json.dumps(_js(script_html))
    result = subprocess.run([node, "-e", code], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert int(result.stdout.strip()) > 0


def test_tooltip_script_ignores_a_target_that_is_not_an_element():
    _run(cost_display.HINT_JS)


def test_nav_script_ignores_a_target_that_is_not_an_element():
    _run(views_base._NAV_TOUCH_JS)


def test_no_page_script_calls_closest_on_a_raw_event_target():
    for path in _LAMBDA.glob("*.py"):
        assert not re.search(r"\be\.target\.closest\(", path.read_text()), path.name
