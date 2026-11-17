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


def web_settings():
    s = WebKit.Settings()
    s.set_enable_developer_extras(False)
    s.set_javascript_can_open_windows_automatically(False)
    return s


def apply_rules(ucm, filters, settings, shields_off=False):
    ucm.remove_all_filters()
    ucm.remove_all_scripts()
    if settings["gpc"]:
        ucm.add_script(WebKit.UserScript.new(GPC_SCRIPT, WebKit.UserContentInjectedFrames.ALL_FRAMES,
                                             WebKit.UserScriptInjectionTime.START, None, None))
    if shields_off:
        return
    for f in filters.active(settings):
        ucm.add_filter(f)
