"""Airlock for Linux: GTK 4 + libadwaita + WebKitGTK."""
import html
import sys
from urllib.parse import parse_qs, urlsplit

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("WebKit", "6.0")
from gi.repository import Adw, Gio, GLib, Gtk, WebKit  # noqa: E402

from . import engine, feeds, netguard, store, urls  # noqa: E402

APP_ID = "io.github.vspayce.Airlock"


def page_html(title, body):
    e = html.escape
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{e(title)}</title>
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<style>:root{{color-scheme:light dark}} body{{font:16px/1.5 system-ui,sans-serif;max-width:620px;margin:12vh auto;padding:0 20px}}
h1{{font-size:24px}} a.btn{{display:inline-block;margin-top:12px;padding:8px 14px;border-radius:10px;background:#33a3a3;color:#fff;text-decoration:none}}
.small{{opacity:.7;font-size:14px}}</style></head><body><h1>{e(title)}</h1>{body}</body></html>"""


class Tab:
    """One web view and what Airlock knows about it."""

    def __init__(self, win, private):
        self.win = win
        self.private = private
        self.session = win.app.sessions.private() if private else win.app.sessions.normal
        self.ucm = WebKit.UserContentManager()
        self.view = WebKit.WebView(network_session=self.session, user_content_manager=self.ucm,
                                   settings=engine.web_settings())
        self.view.set_vexpand(True)
        self.typed = None            # the host the user typed or picked (may be on the local network)
        self.allow_http = set()      # hosts the user chose to load over plain HTTP, this tab only
        self.upgraded = set()        # addresses Airlock switched to HTTPS (a failure there offers HTTP)
        self.internal_ok = False     # Airlock itself is opening one of its own pages
        self.apply_rules()
        v = self.view
        v.connect("decide-policy", self.on_policy)
        v.connect("load-failed-with-tls-errors", self.on_tls_error)
        v.connect("load-failed", self.on_load_failed)
        v.connect("create", self.on_create)
        v.connect("notify::title", lambda *_: win.tab_changed(self))
        v.connect("notify::uri", lambda *_: win.tab_changed(self))
        v.connect("notify::estimated-load-progress", lambda *_: win.tab_changed(self))
        v.connect("notify::is-loading", lambda *_: win.tab_changed(self))

    def apply_rules(self):
        s = self.win.app.settings
        engine.apply_rules(self.ucm, self.win.app.filters, s)

    def load(self, url, typed=False):
        if typed:
            self.typed = urls.host_of(url)
        self.view.load_uri(url)

    def open_internal(self, url):
        self.internal_ok = True
        self.view.load_uri(url)

    def show_html(self, title, body, base="about:blank"):
        # Airlock's own notice pages may link to airlock: pages (e.g. "Load over HTTP anyway").
        self.internal_ok = True
        self.view.load_alternate_html(page_html(title, body), base, None)

    # Navigation rules

    def on_policy(self, view, decision, kind):
        if kind == WebKit.PolicyDecisionType.RESPONSE:
            return False
        action = decision.get_navigation_action()
        req = action.get_request()
        uri = req.get_uri() or ""
        parts = urlsplit(uri)
        scheme = parts.scheme.lower()
        frame_name = action.get_frame_name() if hasattr(action, "get_frame_name") else None
        main_frame = kind == WebKit.PolicyDecisionType.NAVIGATION_ACTION and not frame_name
        if kind == WebKit.PolicyDecisionType.NEW_WINDOW_ACTION:
            return False  # handled in on_create
        if scheme in ("about", "blob", "data") and not main_frame:
            return False
        if scheme == "airlock":
            # Airlock's own pages: only when Airlock opens them, or from another Airlock page.
            current = urlsplit(view.get_uri() or "").scheme
            if current == "airlock" or self.internal_ok:
                return False
            decision.ignore()
            return True
        if main_frame:
            self.internal_ok = False
        if scheme not in ("http", "https"):
            if scheme in ("about",) and uri in ("about:blank",):
                return False
            decision.ignore()
            if main_frame:
                self.win.toast("Airlock only opens web addresses")
            return True
        host = (parts.hostname or "").lower()
        # A website can't send you into your local network; you can still type a local address.
        if netguard.is_local_host(host) and host != self.typed:
            decision.ignore()
            self.win.toast("Blocked a page from reaching your local network")
            return True
        s = self.win.app.settings
        target = uri
        if s["strip_tracking"]:
            target = urls.clean(target)
        # HTTPS only, except addresses you chose to load over HTTP and local devices you typed (a router page).
        typed_local = host == self.typed and netguard.is_local_host(host)
        if s["https_only"] and scheme == "http" and host not in self.allow_http and not typed_local:
            target = urls.upgrade_to_https(target)
            self.upgraded.add(target)
        if target != uri:
            decision.ignore()
            view.load_uri(target)
            return True
        return False

    def on_create(self, view, action):
        # New windows become tabs, and only when you clicked something.
        uri = action.get_request().get_uri()
        if action.is_user_gesture() and urlsplit(uri).scheme in ("http", "https"):
            tab = self.win.new_tab(private=self.private)
            tab.load(uri)
        return None

    def on_tls_error(self, view, failing_uri, cert, errors):
        host = urls.host_of(failing_uri)
        self.show_html("Not secure", f"<p>{html.escape(host)}’s certificate isn’t valid, so Airlock won’t open it.</p>"
                       "<p class='small'>Someone could be pretending to be this site, or the site is set up wrong. "
                       "There’s no option to continue.</p>", failing_uri)
        return True

    def on_load_failed(self, view, event, failing_uri, error):
        # Navigations Airlock redirected (HTTPS upgrade, cleaned links) end their first load this way.
        if error.domain == "WebKitPolicyError" or (error.domain == "WebKitNetworkError" and error.code == 302):
            return False
        parts = urlsplit(failing_uri)
        if failing_uri in self.upgraded:
            plain_url = "http" + failing_uri[5:]
            q = GLib.Uri.escape_string(plain_url, None, False)
            self.show_html("No secure version", f"<p>{html.escape(parts.hostname or '')} didn’t answer over HTTPS. "
                           "Over plain HTTP, anyone on the network can see and change the page.</p>"
                           f"<a class='btn' href='airlock://allow-http?url={html.escape(q)}'>Load over HTTP anyway</a>"
                           f"<p class='small'>{html.escape(error.message)}</p>")
            return True
        return False


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Airlock")
        self.app = app
        self.set_default_size(1200, 820)
        self.tabs = {}
        self.toasts = Adw.ToastOverlay()

        self.tab_view = Adw.TabView()
        self.tab_view.connect("notify::selected-page", lambda *_: self.sync_bar())
        self.tab_view.connect("close-page", self.on_close_page)
        tab_bar = Adw.TabBar(view=self.tab_view, autohide=False)

        # Header: navigation, address bar, tools. In COSMIC and GNOME this is the title bar itself.
        header = Adw.HeaderBar()
        self.back = self._button("go-previous-symbolic", "Back", lambda *_: self.view and self.view.go_back())
        self.fwd = self._button("go-next-symbolic", "Forward", lambda *_: self.view and self.view.go_forward())
        self.reload = self._button("view-refresh-symbolic", "Reload", self.on_reload)
        for b in (self.back, self.fwd, self.reload):
            header.pack_start(b)

        self.entry = Gtk.Entry(hexpand=True, placeholder_text="Search or enter address")
        self.entry.set_input_purpose(Gtk.InputPurpose.URL)
        self.entry.connect("activate", self.on_go)
        self.entry.set_max_width_chars(80)
        clamp = Adw.Clamp(maximum_size=760, child=self.entry, hexpand=True)
        header.set_title_widget(clamp)

        menu = Gio.Menu()
        menu.append("New Private Tab", "app.private-tab")
        menu.append("Feeds", "app.feeds")
        menu.append("Add Page to Feeds", "win.follow")
        menu.append("Settings", "app.settings")
        menu.append("About Airlock", "app.about")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Menu"))
        header.pack_end(self._button("user-trash-full-symbolic", "Burn: close every tab and erase browsing data",
                                     lambda *_: self.app.burn()))
        header.pack_end(self._button("tab-new-symbolic", "New tab", lambda *_: self.new_tab(focus=True)))
        self.engine_button = Gtk.MenuButton(tooltip_text="Search engine")
        header.pack_end(self.engine_button)
        self.build_engine_menu()

        follow = Gio.SimpleAction.new("follow", None)
        follow.connect("activate", lambda *_: self.follow_current())
        self.add_action(follow)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(header)
        box.append(tab_bar)
        box.append(self.tab_view)
        self.toasts.set_child(box)
        self.set_content(self.toasts)

    def _button(self, icon, tip, cb):
        b = Gtk.Button(icon_name=icon, tooltip_text=tip)
        b.connect("clicked", cb)
        return b

    def build_engine_menu(self):
        s = self.app.settings
        menu = Gio.Menu()
        private, public = Gio.Menu(), Gio.Menu()
        for eid, (title, _, is_private) in urls.SEARCH_ENGINES.items():
            item = Gio.MenuItem.new(("✓ " if eid == s["engine"] else "") + urls.engine_label(eid), None)
            item.set_action_and_target_value("app.engine", GLib.Variant.new_string(eid))
            (private if is_private else public).append_item(item)
        menu.append_section("Private", private)
        menu.append_section("Not private (*)", public)
        self.engine_button.set_menu_model(menu)
        self.engine_button.set_label(urls.SEARCH_ENGINES[s["engine"]][0])

    @property
    def current(self):
        page = self.tab_view.get_selected_page()
        return self.tabs.get(page) if page else None

    @property
    def view(self):
        t = self.current
        return t.view if t else None

    def new_tab(self, private=False, url=None, focus=False, typed=False):
        tab = Tab(self, private)
        page = self.tab_view.append(tab.view)
        page.set_title("Private tab" if private else "New tab")
        if private:
            page.set_icon(Gio.ThemedIcon.new("security-high-symbolic"))
        self.tabs[page] = tab
        self.tab_view.set_selected_page(page)
        if url:
            tab.load(url, typed=typed)
        elif focus:
            self.entry.set_text("")
            self.entry.grab_focus()
        return tab

    def on_close_page(self, view, page):
        tab = self.tabs.pop(page, None)
        view.close_page_finish(page, True)
        if tab and tab.private:
            tab.session = None
        if not self.tabs:
            self.new_tab(focus=True)
        return True

    def close_all(self):
        for page in list(self.tabs):
            self.tab_view.close_page(page)

    def tab_changed(self, tab):
        for page, t in self.tabs.items():
            if t is tab:
                title = tab.view.get_title() or urls.host_of(tab.view.get_uri() or "") or "New tab"
                page.set_title(("🔒 " if tab.private else "") + title)
                page.set_loading(tab.view.is_loading())
        if tab is self.current:
            self.sync_bar()

    def sync_bar(self):
        tab = self.current
        if not tab:
            return
        v = tab.view
        uri = v.get_uri() or ""
        if not self.entry.has_focus() or not self.entry.get_text():
            self.entry.set_text("" if uri.startswith("about:") else uri)
        p = v.get_estimated_load_progress()
        self.entry.set_progress_fraction(p if v.is_loading() and p < 1 else 0)
        self.back.set_sensitive(v.can_go_back())
        self.fwd.set_sensitive(v.can_go_forward())
        self.reload.set_icon_name("process-stop-symbolic" if v.is_loading() else "view-refresh-symbolic")

    def on_reload(self, *_):
        v = self.view
        if v:
            v.stop_loading() if v.is_loading() else v.reload()

    def on_go(self, entry):
        target = urls.url_from_input(entry.get_text(), self.app.settings["engine"])
        if not target:
            return
        tab = self.current or self.new_tab()
        tab.load(target, typed=True)
        tab.view.grab_focus()

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=text, timeout=3))

    def follow_current(self):
        tab = self.current
        uri = tab.view.get_uri() if tab else ""
        if not uri.startswith(("https://", "http://")):
            self.toast("Open a web page first")
            return
        self.app.follow(uri, self.toast)


class Application(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.settings = store.Settings()
        self.window = None
        self.feeds = feeds.FeedStore()
        self.feed_message = ""
        self.ready_lists = set()
        self.start_private = False
        self.connect("shutdown", self.on_shutdown)

    def do_startup(self):
        Adw.Application.do_startup(self)
        self.sessions = engine.Sessions()
        self.filters = engine.Filters(self.on_filter_ready)
        ctx = WebKit.WebContext.get_default()
        ctx.register_uri_scheme("airlock", self.on_airlock_scheme)
        ctx.get_security_manager().register_uri_scheme_as_local("airlock")
        ctx.get_security_manager().register_uri_scheme_as_no_access("airlock")

        def act(name, cb, param=None):
            a = Gio.SimpleAction.new(name, param)
            a.connect("activate", cb)
            self.add_action(a)

        act("new-tab", lambda *_: self.window.new_tab(focus=True))
        act("private-tab", lambda *_: self.window.new_tab(private=True, focus=True))
        act("close-tab", lambda *_: self.window.tab_view.close_page(self.window.tab_view.get_selected_page()))
        act("focus-address", lambda *_: (self.window.entry.grab_focus(), self.window.entry.select_region(0, -1)))
        act("reload", lambda *_: self.window.on_reload())
        act("back", lambda *_: self.window.view and self.window.view.go_back())
        act("forward", lambda *_: self.window.view and self.window.view.go_forward())
        act("burn", lambda *_: self.burn())
        act("feeds", lambda *_: self.open_feeds())
        act("settings", lambda *_: self.show_settings())
        act("about", lambda *_: self.show_about())
        act("engine", self.on_engine, GLib.VariantType.new("s"))
        for action, keys in {"app.new-tab": ["<Control>t"], "app.private-tab": ["<Control><Shift>n"],
                             "app.close-tab": ["<Control>w"], "app.focus-address": ["<Control>l", "F6"],
                             "app.reload": ["<Control>r", "F5"], "app.back": ["<Alt>Left"],
                             "app.forward": ["<Alt>Right"], "app.burn": ["<Control><Shift>Delete"]}.items():
            self.set_accels_for_action(action, keys)
        self.settings.on_change(self.on_setting_changed)

    def do_activate(self):
        if not self.window:
            self.window = Window(self)
            self.window.new_tab(private=self.start_private, focus=True)
        self.window.present()

    def do_open(self, files, n, hint):
        self.do_activate()
        for f in files:
            uri = f.get_uri()
            if urlsplit(uri).scheme in ("http", "https"):
                self.window.new_tab(url=uri)

    def on_filter_ready(self, name):
        self.ready_lists.add(name)
        if self.window:
            for tab in self.window.tabs.values():
                tab.apply_rules()

    def on_setting_changed(self, key):
        if key == "engine" and self.window:
            self.window.build_engine_menu()
        if key in ("protection", "block_ads", "easyprivacy", "hide_cookie_banners", "gpc") and self.window:
            for tab in self.window.tabs.values():
                tab.apply_rules()

    def on_engine(self, action, value):
        self.settings["engine"] = value.get_string()

    # Burn

    def burn(self, then=None):
        w = self.window
        w.close_all()

        def done():
            w.toast("Burned: tabs closed and browsing data erased")
            if then:
                then()
        self.sessions.clear_normal(done)

    def on_shutdown(self, *_):
        if self.settings["clear_on_quit"]:
            loop = GLib.MainLoop()
            self.sessions.clear_normal(loop.quit)
            GLib.timeout_add_seconds(5, loop.quit)
            loop.run()

    # Feeds

    def open_feeds(self):
        w = self.window
        tab = w.current if w.current and not (w.current.view.get_uri() or "").startswith("http") else w.new_tab()
        tab.open_internal("airlock://feeds")
        if self.feeds.feeds:
            self.feeds.refresh(lambda: self.reload_feed_tabs())

    def reload_feed_tabs(self):
        for tab in self.window.tabs.values():
            if (tab.view.get_uri() or "").startswith("airlock://feeds"):
                tab.open_internal("airlock://feeds")

    def follow(self, url, report):
        import threading

        def work():
            try:
                title = self.feeds.follow(url)
                msg = f"Added to Feeds: {title}"
            except Exception as e:
                msg = f"Couldn't follow that: {e}"
            GLib.idle_add(lambda: (report(msg), self.reload_feed_tabs(), False)[-1])
        threading.Thread(target=work, daemon=True).start()

    def on_airlock_scheme(self, request):
        uri = request.get_uri()
        parts = urlsplit(uri)
        route = (parts.netloc + parts.path).rstrip("/")
        q = parse_qs(parts.query)
        if route == "feeds/add" and q.get("url"):
            raw = q["url"][0].strip()
            target = raw if "://" in raw else "https://" + raw

            def report(msg):
                self.feed_message = msg
            self.feed_message = "Following…"
            self.follow(target, report)
            return self._redirect(request, "airlock://feeds")
        if route == "feeds/unfollow" and q.get("id"):
            self.feeds.unfollow(q["id"][0])
            return self._redirect(request, "airlock://feeds")
        if route == "feeds/refresh":
            self.feed_message = "Refreshing…"
            self.feeds.refresh(lambda: (setattr(self, "feed_message", ""), self.reload_feed_tabs()))
            return self._redirect(request, "airlock://feeds")
        if route == "feeds":
            msg, self.feed_message = self.feed_message, ""
            return self._finish(request, feeds.render(self.feeds, msg))
        if route == "allow-http" and q.get("url"):
            target = q["url"][0]
            tab = self.window.current
            if tab and urlsplit(target).scheme == "http":
                tab.allow_http.add(urls.host_of(target))
                GLib.idle_add(lambda: (tab.view.load_uri(target), False)[-1])
            return self._finish(request, page_html("Loading…", ""))
        request.finish_error(GLib.Error.new_literal(Gio.io_error_quark(), "Not found", Gio.IOErrorEnum.NOT_FOUND))

    def _finish(self, request, text):
        data = GLib.Bytes.new(text.encode("utf-8"))
        request.finish(Gio.MemoryInputStream.new_from_bytes(data), data.get_size(), "text/html")

    def _redirect(self, request, target):
        page = f'<!doctype html><meta http-equiv="refresh" content="0;url={target}">'
        self._finish(request, page)

    # Dialogs

    def show_settings(self):
        s = self.settings
        dlg = Adw.PreferencesDialog(title="Settings")
        page = Adw.PreferencesPage(title="Privacy", icon_name="security-high-symbolic")

        search = Adw.PreferencesGroup(title="Search")
        ids = list(urls.SEARCH_ENGINES)
        engines = Adw.ComboRow(title="Search engine", subtitle="* = not private",
                               model=Gtk.StringList.new([urls.engine_label(i) for i in ids]))
        engines.set_selected(ids.index(s["engine"]))
        engines.connect("notify::selected", lambda r, _: s.__setitem__("engine", ids[r.get_selected()]))
        search.add(engines)
        page.add(search)

        prot = Adw.PreferencesGroup(title="Tracking protection",
                                    description="Ad blocking uses EasyList and EasyPrivacy, the same community lists uBlock Origin starts with.")
        mode = Adw.ComboRow(title="Protection", subtitle="Strict blocks trackers outright; Standard strips their cookies",
                            model=Gtk.StringList.new(["Standard", "Strict"]))
        mode.set_selected(1 if s["protection"] == "strict" else 0)
        mode.connect("notify::selected", lambda r, _: s.__setitem__("protection", "strict" if r.get_selected() else "standard"))
        prot.add(mode)
        for key, title in (("block_ads", "Block ads (EasyList)"), ("easyprivacy", "Extra tracker list (EasyPrivacy)"),
                           ("hide_cookie_banners", "Hide cookie banners"), ("strip_tracking", "Remove tracking from links"),
                           ("https_only", "HTTPS only"), ("gpc", "Ask sites not to sell or share my data (GPC)"),
                           ("clear_on_quit", "Erase browsing data when Airlock quits")):
            row = Adw.SwitchRow(title=title, active=s[key])
            row.connect("notify::active", lambda r, _, k=key: s.__setitem__(k, r.get_active()))
            prot.add(row)
        page.add(prot)
        dlg.add(page)
        dlg.present(self.window)

    def show_about(self):
        about = Adw.AboutDialog(application_name="Airlock", application_icon=APP_ID, developer_name="Jason Taylor",
                                version="0.1.0", license_type=Gtk.License.GPL_3_0, website="https://vspayce.github.io/Airlock/",
                                comments="A privacy browser: trackers and ads blocked, nothing kept that you don't keep.")
        about.present(self.window)


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    if "--selftest" in argv:
        from . import selftest
        return selftest.run()
    app = Application()
    if "--private" in argv:
        argv.remove("--private")
        app.start_private = True
    return app.run(argv)
