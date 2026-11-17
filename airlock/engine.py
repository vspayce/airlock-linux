"""Blocking rules, browsing sessions and the web views that use them."""
import hashlib
import json
import os
import zlib

from gi.repository import Gio, GLib, WebKit

from . import store

DATA = os.path.join(os.path.dirname(__file__), "data")

GPC_SCRIPT = """
(function () {
  try {
    Object.defineProperty(Navigator.prototype, 'globalPrivacyControl', {
      get: function () { return true; }, configurable: true, enumerable: true
    });
  } catch (e) {}
})();
"""


def _tracker_rules(action):
    lists = json.load(open(os.path.join(DATA, "airlock-lists.json")))
    always = set(lists["alwaysBlocked"])
    rules = []
    for d in lists["domains"]:
        rules.append({
            "trigger": {"url-filter": "^[^:]+://+([^:/]+\\.)?" + d.replace(".", "\\.") + "[:/]",
                        "load-type": ["third-party"]},
            "action": {"type": "block" if d in always else action},
        })
    return json.dumps(rules)


def _banner_rules():
    lists = json.load(open(os.path.join(DATA, "airlock-lists.json")))
    return json.dumps([{"trigger": {"url-filter": ".*"},
                        "action": {"type": "css-display-none", "selector": ", ".join(lists["bannerSelectors"])}}])


def _bundled(name):
    def load():
        with open(os.path.join(DATA, name), "rb") as f:
            return zlib.decompress(f.read(), -15).decode("utf-8")
    return load


class Filters:
    """Compiles each rule list once (WebKit keeps the compiled form on disk) and hands them out by name."""

    LISTS = {
        "standard": lambda: _tracker_rules("block-cookies"),
        "strict": lambda: _tracker_rules("block"),
        "banners": _banner_rules,
        "easylist": _bundled("easylist-1.json.z"),
        "easyprivacy": _bundled("easyprivacy-1.json.z"),
    }

    def __init__(self, on_ready):
        self.store = WebKit.UserContentFilterStore.new(os.path.join(store.CACHE_DIR, "filters"))
        self.compiled = {}
        self.errors = {}
        self.on_ready = on_ready
        for name, source in self.LISTS.items():
            self._prepare(name, source)

    def _key(self, name, source):
        path = os.path.join(DATA, "easylist-1.json.z" if name == "easylist" else "easyprivacy-1.json.z")
        if name in ("easylist", "easyprivacy"):
            digest = hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]
        else:
            digest = hashlib.sha256(source().encode()).hexdigest()[:16]
        return f"airlock-{name}-{digest}"

    def _prepare(self, name, source):
        key = self._key(name, source)

        def loaded(st, res):
            try:
                self._done(name, st.load_finish(res))
            except GLib.Error:
                data = source().encode("utf-8")
                st.save(key, GLib.Bytes.new(data), None, saved)

        def saved(st, res):
            try:
                self._done(name, st.save_finish(res))
            except GLib.Error as e:
                self.errors[name] = e.message
                print(f"airlock: rule list {name} failed: {e.message}", flush=True)
                self.on_ready(name)

        self.store.load(key, None, loaded)

    def _done(self, name, flt):
        self.compiled[name] = flt
        self.on_ready(name)

    def active(self, settings):
        names = ["strict" if settings["protection"] == "strict" else "standard"]
        if settings["hide_cookie_banners"]:
            names.append("banners")
        if settings["block_ads"]:
            names.append("easylist")
        if settings["easyprivacy"]:
            names.append("easyprivacy")
        return [self.compiled[n] for n in names if n in self.compiled]


class Sessions:
    """One lasting session for normal tabs; each private tab gets its own throwaway one."""

    def __init__(self):
        self.normal = WebKit.NetworkSession.new(os.path.join(store.DATA_DIR, "web"),
                                                os.path.join(store.CACHE_DIR, "web"))
        self._harden(self.normal)

    @staticmethod
    def _harden(session):
        session.set_itp_enabled(True)
        session.set_tls_errors_policy(WebKit.TLSErrorsPolicy.FAIL)
        session.get_cookie_manager().set_accept_policy(WebKit.CookieAcceptPolicy.NO_THIRD_PARTY)

    def private(self):
        s = WebKit.NetworkSession.new_ephemeral()
        self._harden(s)
        return s

    def clear_normal(self, done=None):
        def finished(mgr, res):
            try:
                mgr.clear_finish(res)
            except GLib.Error as e:
                print("airlock: clearing data failed:", e.message, flush=True)
            if done:
                done()
        self.normal.get_website_data_manager().clear(WebKit.WebsiteDataTypes.ALL, 0, None, finished)


# The most common WebKit user agent (Safari on a Mac), so Airlock on Linux blends in with far more people.
GENERIC_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
              "Version/18.0 Safari/605.1.15")


def web_settings(settings=None):
    s = WebKit.Settings()
    s.set_enable_developer_extras(False)
    s.set_javascript_can_open_windows_automatically(False)
    apply_user_agent(s, settings)
    return s


def apply_user_agent(web, settings):
    if settings is not None and settings["generic_ua"]:
        web.set_user_agent(GENERIC_UA)
    else:
        web.set_user_agent(None)   # WebKitGTK's own (Safari on Linux)


SCANNER_WORLD = "airlock"


def scanner_script():
    """Reports which tracker domains a page references (runs in a world the page can't see)."""
    domains = json.load(open(os.path.join(DATA, "airlock-lists.json")))["domains"]
    return """
(function () {
  const list = %s;
  const found = new Set();
  function check(u) {
    try {
      const h = new URL(u, location.href).hostname;
      if (!h || h === location.hostname) return;
      for (const d of list) {
        if (h === d || h.endsWith('.' + d)) {
          if (!found.has(d)) { found.add(d); window.webkit.messageHandlers.airlockTrackers.postMessage(d); }
          return;
        }
      }
    } catch (e) {}
  }
  function scan(root) {
    if (!root.querySelectorAll) return;
    root.querySelectorAll('script[src],img[src],iframe[src],link[href]').forEach(function (el) { check(el.src || el.href); });
  }
  function start() {
    scan(document);
    try { performance.getEntriesByType('resource').forEach(function (e) { check(e.name); }); } catch (e) {}
    new MutationObserver(function (ms) {
      ms.forEach(function (m) { m.addedNodes.forEach(function (n) { if (n.nodeType === 1) { check(n.src || n.href || ''); scan(n); } }); });
    }).observe(document.documentElement, { childList: true, subtree: true });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
""" % json.dumps(domains)


_SCANNER = None


PICKER_SCRIPT = r"""
(function () {
  if (window.__airlockPicker) return;
  window.__airlockPicker = true;
  var box = document.createElement('div');
  box.style.cssText = 'position:fixed;pointer-events:none;z-index:2147483647;border:2px solid #33c3c3;' +
    'background:rgba(51,195,195,.15);border-radius:4px;transition:all .05s';
  document.documentElement.appendChild(box);
  var ok = /^[A-Za-z][\w-]*$/;
  function selector(el) {
    var parts = [];
    while (el && el.nodeType === 1 && el !== document.body && el !== document.documentElement && parts.length < 5) {
      if (el.id && ok.test(el.id)) { parts.unshift('#' + el.id); break; }
      var p = el.tagName.toLowerCase();
      var cls = Array.prototype.filter.call(el.classList, function (c) { return ok.test(c); }).slice(0, 2);
      if (cls.length) p += '.' + cls.join('.');
      else { var i = 1, s = el; while ((s = s.previousElementSibling)) if (s.tagName === el.tagName) i++; p += ':nth-of-type(' + i + ')'; }
      parts.unshift(p);
      el = el.parentElement;
    }
    return parts.join(' > ');
  }
  function move(e) {
    var r = e.target.getBoundingClientRect();
    box.style.top = r.top + 'px'; box.style.left = r.left + 'px';
    box.style.width = r.width + 'px'; box.style.height = r.height + 'px';
  }
  function click(e) {
    e.preventDefault(); e.stopPropagation();
    var s = selector(e.target);
    e.target.style.setProperty('display', 'none', 'important');
    done();
    window.webkit.messageHandlers.airlockPick.postMessage(s);
  }
  function key(e) { if (e.key === 'Escape') { e.preventDefault(); done(); } }
  function done() {
    removeEventListener('mousemove', move, true); removeEventListener('click', click, true);
    removeEventListener('keydown', key, true); box.remove(); window.__airlockPicker = false;
  }
  addEventListener('mousemove', move, true); addEventListener('click', click, true); addEventListener('keydown', key, true);
})();
"""


def safe_selector(sel):
    """Saved selectors only ever hide things: nothing that could end the rule and add CSS of its own."""
    sel = (sel or "").strip()
    if not sel or len(sel) > 300 or any(c in sel for c in "{}<>;@\\\n\r\"'") or "/*" in sel:
        return None
    return sel


def apply_rules(ucm, filters, settings, shields_off=False, hidden=()):
    global _SCANNER
    ucm.remove_all_filters()
    ucm.remove_all_scripts()
    if _SCANNER is None:
        _SCANNER = scanner_script()
    ucm.add_script(WebKit.UserScript.new_for_world(_SCANNER, WebKit.UserContentInjectedFrames.TOP_FRAME,
                                                   WebKit.UserScriptInjectionTime.START, SCANNER_WORLD, None, None))
    if settings["gpc"]:
        ucm.add_script(WebKit.UserScript.new(GPC_SCRIPT, WebKit.UserContentInjectedFrames.ALL_FRAMES,
                                             WebKit.UserScriptInjectionTime.START, None, None))
    ucm.remove_all_style_sheets()
    safe = [x for x in (safe_selector(h) for h in hidden) if x]
    if safe:
        css = "\n".join(f"{x} {{ display: none !important; }}" for x in safe)
        ucm.add_style_sheet(WebKit.UserStyleSheet.new(css, WebKit.UserContentInjectedFrames.TOP_FRAME,
                                                      WebKit.UserStyleLevel.USER, None, None))
    if shields_off:
        return
    for f in filters.active(settings):
        ucm.add_filter(f)
