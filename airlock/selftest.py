"""`airlock --selftest`: checks the blocking rules in a real web view, plus the pure-logic checks.

Prints PASS/FAIL lines and exits non-zero on any failure (CI runs it under a virtual display).
"""
import json
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("WebKit", "6.0")
from gi.repository import GLib, Gtk, WebKit  # noqa: E402

from . import engine, logic_tests, store  # noqa: E402

PAGE = """<!doctype html><html><body>
<script>window.r={};function m(k,v){window.r[k]=v}</script>
<script src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js" onload="m('adsense','loaded')" onerror="m('adsense','blocked')"></script>
<script src="https://securepubads.g.doubleclick.net/tag/js/gpt.js" onload="m('gpt','loaded')" onerror="m('gpt','blocked')"></script>
<script src="https://script.ac/airlocktest.js" onload="m('ac','loaded')" onerror="m('ac','blocked')"></script>
<img src="https://www.google-analytics.com/collect?v=1&t=pageview" onload="m('ga','loaded')" onerror="m('ga','blocked')">
<div class="ad-slot" id="slot">Ad</div><div class="article" id="keep">Story</div>
</body></html>"""


def run():
    results = []
    for name, ok, detail in logic_tests.run_all():
        results.append((name, ok, detail))

    Gtk.init()
    settings = store.Settings()
    loop = GLib.MainLoop()
    state = {"ready": set()}
    filters = engine.Filters(lambda n: state["ready"].add(n))
    session = WebKit.NetworkSession.new_ephemeral()
    ucm = WebKit.UserContentManager()
    view = WebKit.WebView(network_session=session, user_content_manager=ucm)
    win = Gtk.Window(child=view)
    win.present()

    def wait_lists():
        if len(state["ready"]) >= len(engine.Filters.LISTS) or state.get("ticks", 0) > 240:
            missing = sorted(set(engine.Filters.LISTS) - set(filters.compiled))
            results.append(("All rule lists compile", not missing,
                            "missing: " + ", ".join(missing) + " " + json.dumps(filters.errors) if missing else "5 lists"))
            engine.apply_rules(ucm, filters, settings)
            view.load_html(PAGE, "https://airlock-selftest.example/")
            GLib.timeout_add_seconds(6, check_page)
            return False
        state["ticks"] = state.get("ticks", 0) + 1
        return True

    def check_page():
        js = ("JSON.stringify({r: window.r || {}, slot: getComputedStyle(document.getElementById('slot')).display,"
              " keep: getComputedStyle(document.getElementById('keep')).display, gpc: navigator.globalPrivacyControl})")

        def done(v, res):
            try:
                out = json.loads(v.evaluate_javascript_finish(res).to_string())
            except Exception as e:  # noqa: BLE001
                results.append(("Read test page", False, str(e)))
                loop.quit()
                return
            r = out["r"]
            for k in ("adsense", "gpt", "ac", "ga"):
                results.append((f"{k} never loads", r.get(k) != "loaded", json.dumps(r)))
            results.append(("Ad slots are hidden", out["slot"] == "none", out["slot"]))
            results.append(("Ordinary content stays visible", out["keep"] != "none", out["keep"]))
            results.append(("Pages see Global Privacy Control", out["gpc"] is True, str(out["gpc"])))
            loop.quit()
        view.evaluate_javascript(js, -1, None, None, None, done)
        return False

    GLib.timeout_add(250, wait_lists)
    GLib.timeout_add_seconds(120, loop.quit)
    loop.run()

    failed = 0
    for name, ok, detail in results:
        print(("PASS  " if ok else "FAIL  ") + name + (f" — {detail}" if detail else ""), flush=True)
        failed += 0 if ok else 1
    print(f"{len(results) - failed} passed, {failed} failed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())
