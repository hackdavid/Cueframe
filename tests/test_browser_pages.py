"""ui.py's page logic against a real headless Chrome (skipped when none is installed)."""

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

CHROME = next(
    (c for c in ("google-chrome", "chromium", "chromium-browser") if shutil.which(c)), None
)
pytestmark = pytest.mark.skipif(CHROME is None, reason="no Chrome/Chromium installed")
UI = Path(__file__).parents[1] / "app/sandbox/bin/ui.py"

PAGES = {
    "banner": """<h1>Shop</h1><button>Go</button>
<div id="ot-banner" style="position:fixed;bottom:0;left:0;right:0;height:120px">
We use cookies. <button>Accept all</button><button>Reject all</button></div>
<script>setTimeout(() => { const b = document.createElement('button');
b.textContent = 'Late'; document.body.appendChild(b) }, 1500)</script>""",
    "dialog": """<h1>Editor</h1><button style="margin:200px">Layer</button>
<div role="dialog" style="position:absolute;inset:0;background:#0008"><p>New Project</p>
<button>Create</button><button aria-label="Close">×</button></div>""",
    "news": """<h1>Docs</h1><button>Close</button>
<div style="position:fixed;top:0">Breaking news <button>Close</button></div>
<a href="#s" style="position:absolute;top:-300px">Skip to search</a>
<x-search></x-search><script>customElements.define('x-search', class extends HTMLElement {
connectedCallback() { this.attachShadow({mode: 'open'}).innerHTML =
'<style>b{color:red}</style><button>Search</button><input placeholder="Find docs">' } })
</script>""",
}


@pytest.fixture(scope="module")
def chrome(tmp_path_factory):
    d = tmp_path_factory.mktemp("pages")
    for k, html in PAGES.items():
        (d / f"{k}.html").write_text(f"<html><body>{html}</body></html>")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    procs = []

    def open_page(name: str) -> None:
        for p in procs:
            p.kill()
            p.wait()
        procs.append(subprocess.Popen(
            [CHROME, "--headless=new", "--no-sandbox", f"--remote-debugging-port={port}",
             f"--user-data-dir={d / 'profile'}", "--window-size=1280,720",
             (d / f"{name}.html").as_uri()],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ))  # fmt: skip
        for _ in range(50):
            if ui("settle").returncode == 0 and ui("text").stdout.startswith("file://"):
                return
            time.sleep(0.2)

    def ui(*args: str) -> subprocess.CompletedProcess:
        env = {**os.environ, "CF_CDP_PORT": str(port)}
        return subprocess.run([sys.executable, str(UI), args[0], "browser", *args[1:]],
                              capture_output=True, text=True, env=env, timeout=40)  # fmt: skip

    yield open_page, ui
    for p in procs:
        p.kill()


def test_consent_banner_is_closed_with_reject_and_late_elements_are_found(chrome):
    open_page, ui = chrome
    open_page("banner")
    pop = ui("popup")
    assert pop.returncode == 0 and pop.stdout.strip().endswith("reject all")
    assert ui("find", "Late", "click").stdout.startswith("__CFBOX ")


def test_a_dialog_over_the_target_is_reported_with_its_buttons(chrome):
    open_page, ui = chrome
    open_page("dialog")
    assert ui("popup").returncode == 1  # a dialog, not a consent banner
    r = ui("find", "Layer", "click")
    assert r.returncode == 3 and "covered by 'new project" in r.stdout and "close" in r.stdout


def test_shadow_dom_is_searched_and_off_page_links_are_not(chrome):
    open_page, ui = chrome
    open_page("news")
    assert ui("popup").returncode == 1  # a news bar's Close is not a consent choice
    r = ui("find", "Search", "click")
    x, y = map(int, r.stdout.split()[1:3])
    assert y > 0  # the shadow-DOM button, not "Skip to search" at top: -300px
    assert ui("find", "Find docs", "type").stdout.startswith("__CFBOX ")
    text = ui("text").stdout
    assert "Search" in text and "color:red" not in text
