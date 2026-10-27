#!/usr/bin/python3
"""Find on-screen elements by their visible words, inside the sandbox.

  ui.py find APP TARGET [click|type]  -> "__CFBOX x y w h" in screen pixels, exit 0
  ui.py text APP                      -> the visible text of APP (page text + URL)

APP "browser" asks Chromium over its DevTools protocol (port 9222); any other app is
read through the Linux accessibility tree (AT-SPI). When nothing matches, exit 3 and
print what *is* on screen, so a failed step can be repaired from real candidates.
Standard library only, except pyatspi for desktop apps."""

import base64
import json
import os
import re
import socket
import struct
import sys
import urllib.request

FIND_JS = r"""
(([target, mode]) => {
  const norm = (s) => (s || "").replace(/\s+/g, " ").trim().toLowerCase();
  const want = norm(target);
  const name = (el) => {
    const lab = el.labels && el.labels.length
      ? [...el.labels].map((l) => l.innerText).join(" ") : "";
    return norm(el.getAttribute("aria-label") || lab || el.getAttribute("placeholder") ||
      el.getAttribute("alt") || el.getAttribute("title") ||
      (["submit", "button"].includes(el.type) ? el.value : "") || el.innerText || "").slice(0, 120);
  };
  const shown = (el) => {
    const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
    return r.width > 2 && r.height > 2 && cs.visibility !== "hidden" && cs.display !== "none";
  };
  const field = (el) =>
    el.matches("input:not([type=hidden]),textarea,select,[contenteditable=true]");
  const active = (el) => field(el) || el.matches("a,button,summary,label,[onclick]," +
    "[role=button],[role=link],[role=tab],[role=menuitem],[role=checkbox],[role=option]");
  const els = [...document.querySelectorAll("body *")].filter(shown);
  let best = null, score = 0;
  if (want === "canvas") {  // the drawing area: largest canvas, preferring ones not covered
    const all = els.filter((e) => e.tagName === "CANVAS");
    const onTop = all.filter((e) => {  // e.g. a dialog's canvas over the editor's
      const r = e.getBoundingClientRect();
      return document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2) === e;
    });
    for (const el of onTop.length ? onTop : all) {
      const r = el.getBoundingClientRect();
      if (r.width * r.height > score) { best = el; score = r.width * r.height; }
    }
    if (best) {
      const r = best.getBoundingClientRect();
      return {found: true, x: r.left, y: r.top, w: r.width, h: r.height, dpr: devicePixelRatio,
              ox: window.screenX, oy: window.screenY + window.outerHeight - window.innerHeight};
    }
  }
  for (const el of els) {
    const n = name(el);
    if (!n) continue;
    let s = n === want ? 30 : n.startsWith(want) ? 20 : n.includes(want) ? 10 : 0;
    if (!s) continue;
    if (mode === "type") s += field(el) ? 8 : -8;
    else s += active(el) ? 6 : 0;
    const r = el.getBoundingClientRect();
    s -= Math.min(5, (r.width * r.height) / 40000);
    if (s > score) { best = el; score = s; }
  }
  if (best && mode === "type" && !field(best)) {
    const inner = best.querySelector("input,textarea,[contenteditable=true]") ||
      (best.htmlFor && document.getElementById(best.htmlFor));
    if (inner) best = inner;
  }
  if (!best) {
    const seen = [...new Set(els.filter(active).map(name).filter(Boolean))].slice(0, 40);
    return {found: false, seen};
  }
  best.scrollIntoView({block: "center", inline: "center"});
  const r = best.getBoundingClientRect();
  return {found: true, x: r.left, y: r.top, w: r.width, h: r.height, dpr: devicePixelRatio,
          ox: window.screenX, oy: window.screenY + window.outerHeight - window.innerHeight};
})
"""
# Typed text lives in field values, not innerText; passwords are never read back.
TEXT_JS = (
    "location.href + '\\n' + document.title + '\\n' + document.body.innerText + '\\n' + "
    "[...document.querySelectorAll('input, textarea, select')]"
    ".filter((e) => !['password', 'hidden'].includes(e.type) && e.value)"
    ".map((e) => e.value).join('\\n')"
)


# ---------- Chromium (DevTools protocol over a minimal websocket) ----------
def _ws(url: str) -> socket.socket:
    host, _, path = url[len("ws://") :].partition("/")
    h, _, p = host.partition(":")
    s = socket.create_connection((h, int(p or 80)), timeout=10)
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall(
        (
            f"GET /{path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ).encode()
    )
    buf = b""
    while b"\r\n\r\n" not in buf:
        buf += s.recv(4096)
    if b" 101 " not in buf.split(b"\r\n")[0]:
        raise RuntimeError("DevTools websocket refused")
    return s


def _send(s: socket.socket, text: str) -> None:
    data, mask = text.encode(), os.urandom(4)
    n = len(data)
    head = struct.pack("!B", 0x81)
    head += (
        struct.pack("!B", 0x80 | n)
        if n < 126
        else (struct.pack("!BH", 0x80 | 126, n) if n < 65536 else struct.pack("!BQ", 0x80 | 127, n))
    )
    s.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))


def _exact(s: socket.socket, n: int) -> bytes:
    out = b""
    while len(out) < n:
        chunk = s.recv(n - len(out))
        if not chunk:
            raise RuntimeError("DevTools connection closed")
        out += chunk
    return out


def _recv(s: socket.socket) -> str:
    msg = b""
    while True:
        b1, b2 = _exact(s, 2)
        n = b2 & 0x7F
        if n == 126:
            n = struct.unpack("!H", _exact(s, 2))[0]
        elif n == 127:
            n = struct.unpack("!Q", _exact(s, 8))[0]
        msg += _exact(s, n)
        if b1 & 0x80:
            return msg.decode(errors="replace")


def cdp_eval(expr: str) -> object:
    pages = json.load(urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=5))
    page = next(p for p in pages if p.get("type") == "page")
    s = _ws(page["webSocketDebuggerUrl"])
    _send(
        s,
        json.dumps(
            {
                "id": 1,
                "method": "Runtime.evaluate",
                "params": {"expression": expr, "returnByValue": True},
            }
        ),
    )
    while True:
        msg = json.loads(_recv(s))
        if msg.get("id") == 1:
            s.close()
            res = msg.get("result", {})
            if "exceptionDetails" in res:
                raise RuntimeError(res["exceptionDetails"].get("text", "page script failed"))
            return res.get("result", {}).get("value")


def browser_find(target: str, mode: str) -> tuple[int, int, int, int] | list[str]:
    r = cdp_eval(f"{FIND_JS}({json.dumps([target, mode])})")
    if not r or not r.get("found"):
        return (r or {}).get("seen", [])
    d = r["dpr"]
    return (
        round((r["ox"] + r["x"]) * d),
        round((r["oy"] + r["y"]) * d),
        round(r["w"] * d),
        round(r["h"] * d),
    )


# ---------- desktop apps (AT-SPI accessibility tree) ----------
def _bus() -> None:
    try:
        with open("/tmp/cf/dbus.env") as f:
            for line in f:
                k, _, v = line.strip().partition("=")
                if k:
                    os.environ[k] = v
    except OSError:
        pass


def _app(name: str):  # noqa: ANN202
    import pyatspi

    desk = pyatspi.Registry.getDesktop(0)
    apps = [a for a in desk if a is not None]
    key = name.lower().replace("-", "")
    for a in apps:
        if key in (a.name or "").lower().replace("-", "").replace(" ", ""):
            return a
    for a in apps:  # else the app with an active window
        for w in a:
            if w is not None and w.getState().contains(pyatspi.STATE_ACTIVE):
                return a
    return None


def _walk(node, out: list, depth: int = 0) -> None:  # noqa: ANN001
    import pyatspi

    if node is None or depth > 40 or len(out) > 4000:
        return
    try:
        st = node.getState()
        # GTK4 may leave SHOWING unset on windows and containers: only prune deeper nodes.
        if depth > 1 and not (
            st.contains(pyatspi.STATE_SHOWING) or st.contains(pyatspi.STATE_VISIBLE)
        ):
            return
        out.append(node)
        for child in node:
            _walk(child, out, depth + 1)
    except Exception:
        return


def _label(node) -> str:  # noqa: ANN001
    n = (node.name or "").strip()
    if not n:
        try:
            t = node.queryText()
            n = t.getText(0, min(t.characterCount, 120)).strip()
        except Exception:
            n = ""
    n = " ".join(n.split()).lower()
    return "" if re.fullmatch(r"(gtk|adw)[a-z]*", n) else n  # widget class, not a label


def desktop_nodes(app: str) -> list:
    _bus()
    a = _app(app)
    if a is None:
        raise RuntimeError(f"No accessible app matching {app!r} is running")
    out: list = []
    for w in a:
        _walk(w, out)
    return out


def desktop_find(app: str, target: str, mode: str) -> tuple[int, int, int, int] | list[str]:
    import pyatspi

    want = " ".join(target.split()).lower()
    fields = {
        pyatspi.ROLE_TEXT,
        pyatspi.ROLE_ENTRY,
        pyatspi.ROLE_PASSWORD_TEXT,
        pyatspi.ROLE_SPIN_BUTTON,
        pyatspi.ROLE_DOCUMENT_TEXT,
        pyatspi.ROLE_PARAGRAPH,
    }
    best, score, seen = None, 0.0, []
    for node in desktop_nodes(app):
        n = _label(node)
        if not n:
            continue
        role = node.getRole()
        acts = node.getState().contains(pyatspi.STATE_FOCUSABLE) or role in fields
        if acts:
            seen.append(n[:60])
        s = 30 if n == want else 20 if n.startswith(want) else 10 if want in n else 0
        if not s:
            continue
        if mode == "type":
            s += 8 if role in fields else -8
        else:
            s += 6 if acts else 0
        ext = node.queryComponent().getExtents(pyatspi.DESKTOP_COORDS)
        if ext.width < 2 or ext.height < 2:
            continue
        s -= min(5, ext.width * ext.height / 40000)
        if s > score:
            best, score = ext, s
    if best is None:
        return list(dict.fromkeys(seen))[:40]
    return best.x, best.y, best.width, best.height


def main() -> int:
    cmd, app = sys.argv[1], sys.argv[2]
    try:
        if cmd == "text":
            if app == "browser":
                print(cdp_eval(TEXT_JS))
            else:
                print("\n".join(dict.fromkeys(filter(None, map(_label, desktop_nodes(app))))))
            return 0
        target, mode = sys.argv[3], (sys.argv[4] if len(sys.argv) > 4 else "click")
        box = browser_find(target, mode) if app == "browser" else desktop_find(app, target, mode)
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}")
        return 4
    if isinstance(box, list):
        print(f"No element matching {target!r} on screen. Visible: " + "; ".join(box))
        return 3
    print("__CFBOX " + " ".join(str(int(v)) for v in box))
    return 0


if __name__ == "__main__":
    sys.exit(main())
