"""forge_ux - the UX and accessibility check forge runs on web projects, in the
forge-sandbox-ux image (Python, Playwright's Chromium, axe-core), offline.

    python forge_ux.py OUT MODE TARGET SCREENS_JSON MAX_PAGES

MODE wsgi:   TARGET is a Python expression giving a WSGI app, evaluated after
             `from module import *`; `tmp` is a scratch folder (a data dir).
MODE static: TARGET is a folder of HTML to serve.
MODE url:    TARGET is a base URL already being served.

It visits the SCREENS paths and every same-site page linked from them (up to
MAX_PAGES), and for each page writes desktop (1280 px) and phone (390 px)
screenshots, runs axe-core (WCAG 2.0-2.2 A/AA and best practice), checks for
sideways scrolling on the phone, and walks the page with Tab: how many
controls are reachable, which show no focus ring, and whether focus gets
stuck. Results: OUT/ux/ux-report.json and ux-report.md, OUT/ux/*.png.
Nothing here is pass/fail - forge reports it.
"""

from __future__ import annotations

import http.server
import json
import os
import re
import socket
import sys
import tempfile
import threading
import urllib.parse
from pathlib import Path

AXE = Path(os.environ.get("FORGE_AXE", "/opt/axe/axe.min.js"))
VIEWPORTS = {"desktop": (1280, 800), "mobile": (390, 844)}
AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"]
SKIP = re.compile(r"(logout|log-out|signout|sign-out|delete|remove|destroy|reset)", re.I)
ASSET = re.compile(r"\.(css|js|png|jpe?g|gif|svg|webp|ico|zip|json|txt|xml|pdf|woff2?|mp3|ogg|wav)$", re.I)
MAX_TABS = 60
MAX_SHOT_HEIGHT = 4000


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def serve_wsgi(expr: str) -> str:
    from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server
    from socketserver import ThreadingMixIn
    sys.path.insert(0, os.getcwd())
    ns: dict = {"tmp": tempfile.mkdtemp(prefix="ux-data-")}
    exec("from module import *", ns)
    app = eval(expr, ns)

    class Quiet(WSGIRequestHandler):
        def log_message(self, *a):
            pass

    class Threaded(ThreadingMixIn, WSGIServer):
        daemon_threads = True

    port = free_port()
    httpd = make_server("127.0.0.1", port, app, server_class=Threaded, handler_class=Quiet)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}"


def serve_static(folder: str) -> str:
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=folder, **kw)

        def log_message(self, *a):
            pass

    port = free_port()
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), Quiet)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}"


def slug(path: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-")
    return s or "home"


FOCUS_JS = """() => {
  const el = document.activeElement;
  if (!el || el === document.body) return null;
  const cs = getComputedStyle(el);
  const ring = (cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0)
            || (cs.boxShadow && cs.boxShadow !== 'none');
  const r = el.getBoundingClientRect();
  const label = (el.getAttribute('aria-label') || el.innerText || el.value || el.name || '').trim().slice(0, 60);
  return {tag: el.tagName.toLowerCase(), id: el.id || '', label: label,
          ring: ring, visible: r.width > 0 && r.height > 0,
          key: el.tagName + '#' + el.id + '|' + label + '|' + Math.round(r.x) + ',' + Math.round(r.y)};
}"""

FACTS_JS = """() => ({
  title: document.title,
  lang: document.documentElement.getAttribute('lang') || '',
  h1: document.querySelectorAll('h1').length,
  main: document.querySelectorAll('main, [role=main]').length,
  scroll: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
  links: Array.from(document.querySelectorAll('a[href]')).map(a => a.getAttribute('href'))
})"""


def keyboard_walk(page) -> dict:
    page.evaluate("() => { if (document.activeElement) document.activeElement.blur(); window.scrollTo(0, 0); }")
    seen, order, invisible, trap, repeat = set(), [], [], False, 0
    last = None
    for _ in range(MAX_TABS):
        page.keyboard.press("Tab")
        info = page.evaluate(FOCUS_JS)
        if info is None:
            if order:
                break
            continue
        if info["key"] == last:
            repeat += 1
            if repeat >= 3:
                trap = True
                break
            continue
        repeat = 0
        last = info["key"]
        if info["key"] in seen:
            break                     # wrapped around to the start
        seen.add(info["key"])
        order.append(f"{info['tag']}: {info['label'] or info['id']}")
        if not info["ring"] or not info["visible"]:
            invisible.append(f"{info['tag']}: {info['label'] or info['id'] or '(no label)'}"
                             + ("" if info["visible"] else " (hidden while focused)"))
    first = order[0].lower() if order else ""
    return {"focusable": len(order), "focus_invisible": invisible[:20], "keyboard_trap": trap,
            "skip_link": "skip" in first, "tab_order": order[:30]}


def run_axe(page) -> list[dict]:
    if not AXE.exists():
        return [{"id": "axe-missing", "impact": None, "help": f"axe-core not found at {AXE}",
                 "nodes": 0, "targets": []}]
    page.add_script_tag(path=str(AXE))
    res = page.evaluate("tags => axe.run(document, {runOnly: {type: 'tag', values: tags}})", AXE_TAGS)
    out = []
    for v in res.get("violations", []):
        out.append({"id": v.get("id"), "impact": v.get("impact"), "help": v.get("help"),
                    "url": v.get("helpUrl"), "nodes": len(v.get("nodes") or []),
                    "targets": [", ".join(n.get("target") or []) for n in (v.get("nodes") or [])[:3]]})
    return out


def main(argv: list[str]) -> int:
    out_dir, mode, target = Path(argv[0]) / "ux", argv[1], argv[2]
    screens = json.loads(argv[3]) if len(argv) > 3 and argv[3] else []
    max_pages = int(argv[4]) if len(argv) > 4 else 12
    out_dir.mkdir(parents=True, exist_ok=True)
    base = {"wsgi": serve_wsgi, "static": serve_static}.get(mode, lambda t: t.rstrip("/"))(target)
    from playwright.sync_api import sync_playwright
    queue = [s["path"] for s in screens if isinstance(s, dict) and s.get("path")] or ["/"]
    names = {s["path"]: s.get("name") for s in screens if isinstance(s, dict) and s.get("path")}
    seen, pages, errors = set(), [], []
    host = urllib.parse.urlsplit(base).netloc
    with sync_playwright() as pw:
        browser = pw.chromium.launch(chromium_sandbox=False, args=["--disable-dev-shm-usage"])
        try:
            ctx = {k: browser.new_context(viewport={"width": w, "height": h}, reduced_motion="reduce")
                   for k, (w, h) in VIEWPORTS.items()}
            while queue and len(pages) < max_pages:
                path = queue.pop(0)
                if path in seen:
                    continue
                seen.add(path)
                rec = {"path": path, "name": names.get(path) or path, "screenshots": []}
                try:
                    for view, context in ctx.items():
                        page = context.new_page()
                        console = []
                        page.on("console", lambda m, c=console: c.append(m.text) if m.type == "error" else None)
                        resp = page.goto(base + path, wait_until="networkidle", timeout=20000)
                        rec.setdefault("status", resp.status if resp else None)
                        facts = page.evaluate(FACTS_JS)
                        shot = out_dir / f"{slug(path)}-{view}.png"
                        height = page.evaluate("() => document.documentElement.scrollHeight")
                        w, h = VIEWPORTS[view]
                        page.screenshot(path=str(shot), full_page=height <= MAX_SHOT_HEIGHT,
                                        clip=None if height <= MAX_SHOT_HEIGHT else
                                        {"x": 0, "y": 0, "width": w, "height": MAX_SHOT_HEIGHT})
                        rec["screenshots"].append(shot.name)
                        if view == "desktop":
                            rec.update(title=facts["title"], lang=facts["lang"], h1_count=facts["h1"],
                                       has_main=facts["main"] > 0, console_errors=console[:10])
                            rec["axe"] = run_axe(page)
                            rec.update(keyboard_walk(page))
                            for href in facts["links"]:
                                u = urllib.parse.urlsplit(urllib.parse.urljoin(base + path, href))
                                if u.netloc != host or u.scheme not in ("http", "https"):
                                    continue
                                p = u.path or "/"
                                if ASSET.search(p) or SKIP.search(p + "?" + u.query):
                                    continue
                                if p not in seen and p not in queue:
                                    queue.append(p)
                        else:
                            rec["horizontal_scroll_mobile"] = facts["scroll"]
                        page.close()
                except Exception as exc:                    # one page must not stop the rest
                    rec["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
                    errors.append(f"{path}: {rec['error']}")
                pages.append(rec)
        finally:
            browser.close()
    report = {"base": base, "pages": pages, "errors": errors}
    (out_dir / "ux-report.json").write_text(json.dumps(report, indent=2))
    (out_dir / "ux-report.md").write_text(report_md(report))
    print(f"ux: {len(pages)} page(s) checked")
    return 0


def report_md(report: dict) -> str:
    lines = ["# UX and accessibility check", "",
             "Automated: axe-core (WCAG 2.2 A/AA and best practice), a keyboard walk with Tab, "
             "and phone-width layout. Screenshots are beside this file.", ""]
    for p in report["pages"]:
        lines += [f"## {p.get('name') or p['path']} ({p['path']})", ""]
        if p.get("error"):
            lines += [f"- Could not check: {p['error']}", ""]
            continue
        lines.append(f"- Title: {p.get('title') or '(none)'}; lang: {p.get('lang') or '(none)'}; "
                     f"h1: {p.get('h1_count')}; main landmark: {'yes' if p.get('has_main') else 'no'}")
        lines.append(f"- Keyboard: {p.get('focusable', 0)} control(s) reachable"
                     + (", skip link first" if p.get("skip_link") else ", no skip link")
                     + ("; FOCUS TRAPPED" if p.get("keyboard_trap") else ""))
        for f in p.get("focus_invisible") or []:
            lines.append(f"  - no visible focus: {f}")
        if p.get("horizontal_scroll_mobile"):
            lines.append("- Phone width (390 px): the page scrolls sideways")
        for v in p.get("axe") or []:
            lines.append(f"- {v['impact'] or 'n/a'}: {v['help']} ({v['id']}, {v['nodes']} element(s)"
                         + (f": {'; '.join(v['targets'])}" if v.get("targets") else "") + ")")
        for c in p.get("console_errors") or []:
            lines.append(f"- console error: {c}")
        lines.append(f"- Screenshots: {', '.join(p.get('screenshots') or [])}")
        lines.append("")
    for e in report.get("errors") or []:
        lines.append(f"- error: {e}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
