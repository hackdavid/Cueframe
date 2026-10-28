#!/usr/bin/python3
"""Find on-screen elements by their visible words, inside the sandbox.

  ui.py find APP TARGET [click|type]  -> "__CFBOX x y w h" in screen pixels, exit 0
  ui.py text APP                      -> the visible text of APP (page text + URL)
  ui.py settle browser                -> returns once the page has loaded and stopped changing
  ui.py popup browser                 -> "__CFPOP x y w h label": the privacy-preserving
                                         button (Reject all, Dismiss...) of a cookie or
                                         consent banner on screen, exit 0; exit 1 if none

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
import time
import urllib.request

# Shared page helpers: elements inside (open) shadow DOM too, and what is really on top.
DEEP_JS = r"""
  const kids = (root) => {
    const out = [];
    const walk = (r) => {
      for (const e of r.querySelectorAll("*")) {
        out.push(e);
        if (e.shadowRoot) walk(e.shadowRoot);
      }
    };
    walk(root);
    return out;
  };
  const up = (e) => e.parentElement || (e.parentNode && e.parentNode.host) || null;
  const within = (a, b) => {
    for (let e = b; e; e = up(e)) if (e === a) return true;
    return false;
  };
  const hitAt = (x, y) => {
    let h = document.elementFromPoint(x, y);
    while (h && h.shadowRoot) {
      const d = h.shadowRoot.elementFromPoint(x, y);
      if (!d || d === h) break;
      h = d;
    }
    return h;
  };
  const textOf = (e) => (e.innerText || "") + kids(e).filter((k) => k.shadowRoot)
    .map((k) => [...k.shadowRoot.children].filter((c) => !c.matches("style,script,link"))
      .map((c) => c.innerText || "").join("\n")).join("\n");
"""
FIND_JS = r"""
(([target, mode]) => {
  /*DEEP*/
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
  // Off the page entirely (e.g. "Skip to search" links parked at top: -300px) can't be used.
  const onPage = (el) => {
    const r = el.getBoundingClientRect();
    return r.bottom + scrollY > 0 && r.right + scrollX > 0;
  };
  const els = kids(document.body).filter(shown).filter(onPage);
  let best = null, score = 0;
  if (want === "canvas") {  // the drawing area: largest canvas, preferring ones not covered
    const all = els.filter((e) => e.tagName === "CANVAS");
    const onTop = all.filter((e) => {  // e.g. a dialog's canvas over the editor's
      const r = e.getBoundingClientRect();
      return hitAt(r.left + r.width / 2, r.top + r.height / 2) === e;
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
    const q = "input,textarea,[contenteditable=true]";
    const inner = best.querySelector(q) || (best.shadowRoot && best.shadowRoot.querySelector(q)) ||
      (best.htmlFor && document.getElementById(best.htmlFor));
    if (inner) best = inner;
  }
  if (!best) {
    const seen = [...new Set(els.filter(active).map(name).filter(Boolean))].slice(0, 40);
    return {found: false, seen};
  }
  best.scrollIntoView({block: "center", inline: "center"});
  const r = best.getBoundingClientRect();
  // Something else on top (a dialog, a banner) would get the click: say what it is.
  const hit = hitAt(r.left + r.width / 2, r.top + r.height / 2);
  if (hit && !within(best, hit) && !within(hit, best)) {
    let cover = hit;
    while (up(cover) && !within(up(cover), best)) cover = up(cover);
    const c = cover.getBoundingClientRect(), cs = getComputedStyle(cover);
    const big = c.width * c.height >= Math.max(4 * r.width * r.height, 30000);
    if (big || cs.position === "fixed" || cover.matches("[role=dialog],[aria-modal=true]")) {
      const inside = kids(cover).filter(shown).filter(active);
      return {found: false, covered: norm(textOf(cover)).slice(0, 160),
              seen: [...new Set(inside.map(name).filter(Boolean))].slice(0, 30)};
    }
  }
  return {found: true, x: r.left, y: r.top, w: r.width, h: r.height, dpr: devicePixelRatio,
          ox: window.screenX, oy: window.screenY + window.outerHeight - window.innerHeight};
})
"""
# The banner's least-sharing choice, never "accept"; only a button that is really on top.
POPUP_JS = r"""
(() => {
  /*DEEP*/
  const norm = (s) => (s || "").replace(/\s+/g, " ").trim().toLowerCase();
  const KEY = /cookie|consent|gdpr|privacy|personal (data|info)|tracking|your choices/i;
  const MARK = /cookie|consent|cmp|gdpr|onetrust|didomi|usercentrics|cookiebot|truste|sp_message/i;
  const PICK = ["reject all", "reject", "decline all", "decline", "do not consent",
    "don't consent", "refuse all", "refuse", "only necessary", "necessary only",
    "use necessary cookies only", "only essential", "essential only",
    "continue without accepting", "dismiss", "close", "no thanks", "not now", "×", "✕", "x"];
  const cls = (e) => (e.id || "") + " " + (typeof e.className === "string" ? e.className : "");
  const view = innerWidth * innerHeight;
  const banner = (el) => {
    for (let e = el; e && e !== document.body; e = up(e)) {
      const cs = getComputedStyle(e), r = e.getBoundingClientRect();
      const marked = MARK.test(cls(e)) || e.matches("[role=dialog],[aria-modal=true]");
      if (marked || ((cs.position === "fixed" || cs.position === "sticky") &&
                     r.width * r.height < 0.6 * view))
        return KEY.test(norm(textOf(e)).slice(0, 3000) + " " + cls(e)) ? e : null;
    }
    return null;
  };
  let best = null, rank = PICK.length;
  const sel = "button,a,[role=button],input[type=button],input[type=submit]";
  for (const b of kids(document).filter((e) => e.matches(sel))) {
    const r = b.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    const lab = norm(b.getAttribute("aria-label") || b.innerText || b.value || b.title);
    const k = PICK.findIndex((p) => lab === p || (p.length > 3 && lab.startsWith(p + " ")));
    if (k < 0 || k >= rank || !banner(b)) continue;
    const hit = hitAt(r.left + r.width / 2, r.top + r.height / 2);
    if (!hit || !(within(b, hit) || within(hit, b))) continue;
    best = b; rank = k;
  }
  if (!best) return null;
  const r = best.getBoundingClientRect();
  return {label: PICK[rank], x: r.left, y: r.top, w: r.width, h: r.height, dpr: devicePixelRatio,
          ox: window.screenX, oy: window.screenY + window.outerHeight - window.innerHeight};
})()
"""
SETTLE_JS = (
    "[document.readyState, document.body ? document.body.innerText.length : 0, "
    "document.getElementsByTagName('*').length]"
)
# Typed text lives in field values, not innerText; passwords are never read back.
TEXT_JS = r"""
(() => {
  /*DEEP*/
  return location.href + "\n" + document.title + "\n" + textOf(document.body) + "\n" +
    kids(document).filter((e) => e.matches("input,textarea,select"))
      .filter((e) => !["password", "hidden"].includes(e.type) && e.value)
      .map((e) => e.value).join("\n");
})()
"""
FIND_JS, POPUP_JS, TEXT_JS = (
    js.replace("/*DEEP*/", DEEP_JS) for js in (FIND_JS, POPUP_JS, TEXT_JS)
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
    pages = json.load(
        urllib.request.urlopen(
            f"http://127.0.0.1:{os.environ.get('CF_CDP_PORT', '9222')}/json", timeout=5
        )
    )
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


def _screen(r: dict) -> tuple[int, int, int, int]:
    d = r["dpr"]
    return (
        round((r["ox"] + r["x"]) * d),
        round((r["oy"] + r["y"]) * d),
        round(r["w"] * d),
        round(r["h"] * d),
    )


def browser_find(target: str, mode: str) -> tuple[int, int, int, int] | list[str] | str:
    """The element's screen box, or what is visible (a list), or what covers it (a str)."""
    r = cdp_eval(f"{FIND_JS}({json.dumps([target, mode])})")
    if r and r.get("covered") is not None:
        return f"{r['covered']!r}. Its buttons: " + "; ".join(r.get("seen", []))
    if not r or not r.get("found"):
        return (r or {}).get("seen", [])
    return _screen(r)


def browser_settle(limit: float = 15) -> bool:
    """Wait until the page has loaded and its text and elements stop changing."""
    end, prev, same = time.time() + limit, None, 0
    while time.time() < end:
        try:
            cur = cdp_eval(SETTLE_JS)
        except Exception:  # no page yet, or it is navigating
            cur = None
        same = same + 1 if cur and cur[0] == "complete" and cur == prev else 0
        if same >= 2:
            return True
        prev = cur
        time.sleep(0.4)
    return False


def browser_popup() -> tuple[str, tuple[int, int, int, int]] | None:
    r = cdp_eval(POPUP_JS)
    return (r["label"], _screen(r)) if r else None


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
        if cmd == "settle":
            return 0 if browser_settle() else print("still loading after 15 s") or 0
        if cmd == "popup":
            hit = browser_popup()
            if not hit:
                return 1
            print("__CFPOP " + " ".join(str(v) for v in hit[1]) + f" {hit[0]}")
            return 0
        if cmd == "text":
            if app == "browser":
                print(cdp_eval(TEXT_JS))
            else:
                print("\n".join(dict.fromkeys(filter(None, map(_label, desktop_nodes(app))))))
            return 0
        target, mode = sys.argv[3], (sys.argv[4] if len(sys.argv) > 4 else "click")
        if app == "browser":
            browser_settle()
        end = time.time() + 5  # an element may show up a moment later (a menu, a result)
        while True:
            box = (
                browser_find(target, mode) if app == "browser" else desktop_find(app, target, mode)
            )
            if isinstance(box, tuple) or time.time() > end:
                break
            time.sleep(0.5)
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}")
        return 4
    if isinstance(box, str):
        print(f"{target!r} is on screen but covered by {box}")
        return 3
    if isinstance(box, list):
        print(f"No element matching {target!r} on screen. Visible: " + "; ".join(box))
        return 3
    print("__CFBOX " + " ".join(str(int(v)) for v in box))
    return 0


if __name__ == "__main__":
    sys.exit(main())
