"""Airlock for Linux: GTK 4 + libadwaita + WebKitGTK."""
import html
import json
import os
import sys
from urllib.parse import parse_qs, urlsplit

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("WebKit", "6.0")
from gi.repository import Adw, Gio, GLib, Gtk, WebKit  # noqa: E402

from . import clip, engine, feeds, netguard, store, urls  # noqa: E402

APP_ID = "io.github.vspayce.Airlock"


def page_html(title, body):
    e = html.escape
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{e(title)}</title>
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<style>:root{{color-scheme:light dark}} body{{font:16px/1.5 system-ui,sans-serif;max-width:620px;margin:12vh auto;padding:0 20px}}
h1{{font-size:24px}} a.btn{{display:inline-block;margin-top:12px;padding:8px 14px;border-radius:10px;background:#33a3a3;color:#fff;text-decoration:none}}
.small{{opacity:.7;font-size:14px}}</style></head><body><h1>{e(title)}</h1>{body}</body></html>"""


def _tile_color(text):
    palette = ["#2f7f86", "#5b6fd6", "#c2683a", "#3f8f55", "#9b59b6", "#b94a6a", "#8a7a2f", "#3a76b8"]
    return palette[sum(map(ord, text)) % len(palette)]


def start_page(settings, tab):
    """New-tab page: the logo, the protection mode button, favorites and a search box."""
    e = html.escape
    private = tab is not None and tab.private
    engine_name = urls.SEARCH_ENGINES[settings["engine"]][0]
    if store.is_hardened(settings):
        status = "Fully Sealed"
    else:
        status = "Sealed · " + ("Strict" if settings["protection"] == "strict" else "Standard")
    current = "hardened" if store.is_hardened(settings) else settings["protection"]
    modes = "".join(
        f'<a class="mode{" on" if key == current else ""}" href="airlock://mode?m={key}"><b>{name}</b><span>{desc}</span></a>'
        for key, name, desc in (("standard", "Standard", "Trackers can't follow you; most sites just work"),
                                ("strict", "Strict", "Trackers blocked outright; a few sites may break"),
                                ("hardened", "Hardened", "Strict, plus everything erased on quit and a common user agent")))
    tiles = "".join(
        f'<div class="tile"><a href="{e(f["url"])}"><span class="ic" style="background:{_tile_color(urls.site_key(urls.host_of(f["url"])))}">'
        f'{e((urls.site_key(urls.host_of(f["url"])) or "?")[:1].upper())}</span><span class="t">{e(f["title"][:24])}</span></a>'
        f'<a class="x" title="Remove" href="airlock://favorites/remove?url={e(GLib.Uri.escape_string(f["url"], None, False))}">×</a></div>'
        for f in settings["favorites"][:24] if urls.host_of(f.get("url", "")))
    if not tiles:
        tiles = '<p class="hint">Add sites here with <b>Add to Favorites</b> in the menu (Ctrl+D).</p>'
    chip = ('<span class="chip private">Private tab · nothing kept</span>' if private else
            f'<details><summary class="chip">{e(status)} ▾</summary><div class="menu">{modes}</div></details>')
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>New tab</title>
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src airlock:; style-src 'unsafe-inline'; form-action airlock:">
<style>
:root {{ color-scheme: light dark; --teal: #2a8f8f; --card: #0000000a; }}
@media (prefers-color-scheme: dark) {{ :root {{ --teal: #62d6d6; --card: #ffffff0d; }} body {{ background: #121722; color: #e2e7ee; }} }}
body {{ font: 16px/1.5 system-ui, sans-serif; margin: 0; min-height: 100vh; display: grid; place-items: center; }}
main {{ text-align: center; width: min(640px, 92vw); padding: 6vh 0; }}
img.logo {{ width: 88px; height: 88px; border-radius: 20px; box-shadow: 0 12px 32px #0004; }}
h1 {{ font-size: 28px; margin: 12px 0 8px; letter-spacing: -0.02em; }}
details {{ display: inline-block; position: relative; }}
summary {{ list-style: none; cursor: pointer; }} summary::-webkit-details-marker {{ display: none; }}
.chip {{ display: inline-flex; gap: 6px; align-items: center; padding: 4px 12px; border-radius: 999px;
  background: color-mix(in srgb, var(--teal) 16%, transparent); color: var(--teal); font-weight: 600; font-size: 14px; }}
.chip.private {{ background: #8a5cf622; color: #a78bfa; }}
.menu {{ position: absolute; left: 50%; transform: translateX(-50%); top: 34px; z-index: 2; width: 300px; text-align: left;
  background: Canvas; border: 1px solid #8884; border-radius: 16px; padding: 6px; box-shadow: 0 12px 32px #0005; }}
.mode {{ display: block; padding: 8px 12px; border-radius: 12px; color: inherit; text-decoration: none; }}
.mode:hover {{ background: var(--card); }} .mode.on {{ background: color-mix(in srgb, var(--teal) 16%, transparent); }}
.mode b {{ display: block; }} .mode span {{ font-size: 13px; opacity: .7; }}
form {{ margin: 24px 0 28px; }}
input {{ width: 100%; box-sizing: border-box; font: inherit; padding: 12px 18px; border-radius: 999px;
  border: 1px solid #8884; background: var(--card); color: inherit; outline: none; }}
input:focus {{ border-color: var(--teal); }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(92px, 1fr)); gap: 14px; }}
.tile {{ position: relative; }} .tile a {{ color: inherit; text-decoration: none; }}
.ic {{ display: grid; place-items: center; width: 60px; height: 60px; margin: 0 auto 6px; border-radius: 16px;
  color: #fff; font-size: 26px; font-weight: 700; }}
.t {{ display: block; font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.x {{ position: absolute; top: -6px; right: 6px; display: none; width: 20px; height: 20px; line-height: 18px;
  border-radius: 50%; background: #8886; color: #fff !important; font-size: 14px; }}
.tile:hover .x {{ display: block; }} .hint {{ opacity: .6; font-size: 14px; }}
</style></head><body><main>
<img class="logo" src="airlock://logo.png" alt="">
<h1>Airlock</h1>
{chip}
<form action="airlock://search" method="get"><input name="q" placeholder="Search {e(engine_name)} or enter address" autocomplete="off" autofocus></form>
<div class="grid">{tiles}</div>
</main></body></html>"""


class Tab:
    """One web view and what Airlock knows about it."""

    def __init__(self, win, private):
        self.win = win
        self.private = private
        self.session = win.app.sessions.private() if private else win.app.sessions.normal
        self.ucm = WebKit.UserContentManager()
        self.view = WebKit.WebView(network_session=self.session, user_content_manager=self.ucm,
                                   settings=engine.web_settings(win.app.settings))
        self.view.set_vexpand(True)
        self.typed = None            # the host the user typed or picked (may be on the local network)
        self.allow_http = set()      # hosts the user chose to load over plain HTTP, this tab only
        self.upgraded = set()        # addresses Airlock switched to HTTPS (a failure there offers HTTP)
        self.internal_ok = False     # Airlock itself is opening one of its own pages
        self.trackers = set()        # tracker domains this page referenced (all blocked or cookie-stripped)
        self.rules_host = None       # the site the current blocking rules were set up for
        self.ucm.register_script_message_handler("airlockTrackers", engine.SCANNER_WORLD)
        self.ucm.connect("script-message-received::airlockTrackers", self.on_tracker)
        self.ucm.register_script_message_handler("airlockPick", engine.SCANNER_WORLD)
        self.ucm.connect("script-message-received::airlockPick", self.on_pick)
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
        v.connect("load-changed", self.on_load_changed)

    def apply_rules(self, host=None):
        s = self.win.app.settings
        host = host if host is not None else urls.host_of(self.view.get_uri() or "")
        self.rules_host = host
        engine.apply_rules(self.ucm, self.win.app.filters, s, shields_off=self.win.app.shields_off(host),
                           hidden=s["hidden"].get(urls.site_key(host), []))

    def on_tracker(self, ucm, value):
        try:
            self.trackers.add(value.to_string())
        except Exception:  # noqa: BLE001
            return
        self.win.tab_changed(self)

    def pick(self):
        self.view.evaluate_javascript(engine.PICKER_SCRIPT, -1, engine.SCANNER_WORLD, None, None, None)
        self.win.toast("Click the item to hide. Esc cancels.")

    def on_pick(self, ucm, value):
        sel = engine.safe_selector(value.to_string())
        site = urls.site_key(urls.host_of(self.view.get_uri() or ""))
        if not sel or not site:
            return
        s = self.win.app.settings
        hidden = dict(s["hidden"])
        hidden[site] = (hidden.get(site, []) + [sel])[-200:]
        s["hidden"] = hidden
        self.win.toast(f"Hidden on {site}. Show it again from the menu.")

    def on_load_changed(self, view, event):
        if event == WebKit.LoadEvent.STARTED:
            self.trackers.clear()
            self.win.tab_changed(self)

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
        if main_frame and self.win.app.shields_off(host) != self.win.app.shields_off(self.rules_host or ""):
            self.apply_rules(host)
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


class SiteInfo(Adw.Dialog):
    """Certificate, domain registration (whois), DNS records and hosting for the current site."""

    def __init__(self, win, tab):
        super().__init__(title="Site info", content_width=520, content_height=640)
        self.win = win
        uri = tab.view.get_uri() or ""
        self.host = urls.host_of(uri)
        page = Adw.PreferencesPage()
        tv = Adw.ToolbarView(content=page)
        tv.add_top_bar(Adw.HeaderBar())
        self.set_child(tv)

        # Connection and certificate (from the page actually loaded, not a second connection).
        conn = Adw.PreferencesGroup(title="Connection", description=self.host)
        ok, cert, errors = tab.view.get_tls_info() if uri.startswith("https://") else (False, None, 0)
        if not uri.startswith("https://"):
            conn.add(self._row("Encryption", "None: plain HTTP. Anyone on the network can read and change this page."))
        elif cert is None:
            conn.add(self._row("Certificate", "Not available"))
        else:
            conn.add(self._row("Encryption", "HTTPS, certificate verified" if not errors else "HTTPS, certificate problems"))
            self._cert_rows(conn, cert)
        page.add(conn)

        self.domain = Adw.PreferencesGroup(title="Domain (whois)")
        self._looking = self._row("Looking up…", "")
        self.domain.add(self._looking)
        page.add(self.domain)
        self.records = Adw.PreferencesGroup(title="DNS records")
        page.add(self.records)
        self.hosts = Adw.PreferencesGroup(title="Hosted by")
        page.add(self.hosts)
        about = Adw.PreferencesGroup(description="Looked up only when you open this. Domain details come from the "
                                                 "registry's RDAP service (the modern whois), DNS records from Cloudflare's "
                                                 "encrypted DNS. No cookies, nothing about you is sent.")
        page.add(about)
        import threading
        threading.Thread(target=self._lookup, daemon=True).start()

    @staticmethod
    def _row(title, value):
        row = Adw.ActionRow(title=GLib.markup_escape_text(title), subtitle=GLib.markup_escape_text(value or ""))
        row.set_subtitle_selectable(True)
        row.add_css_class("property")
        return row

    def _cert_rows(self, group, cert):
        import hashlib

        def name(n):
            return n or "—"
        p = cert.props
        group.add(self._row("Issued to", name(getattr(p, "subject_name", None))))
        group.add(self._row("Issued by", name(getattr(p, "issuer_name", None))))
        try:
            nb, na = p.not_valid_before, p.not_valid_after
            if na:
                left = (na.to_unix() - GLib.DateTime.new_now_utc().to_unix()) // 86400
                group.add(self._row("Valid", f"{nb.format('%b %-d, %Y') if nb else '?'} to {na.format('%b %-d, %Y')} ({left} days left)"))
        except (AttributeError, TypeError):
            pass
        chain, c = [], cert.get_issuer()
        while c is not None and len(chain) < 5:
            chain.append(getattr(c.props, "subject_name", None) or "?")
            c = c.get_issuer()
        if chain:
            group.add(self._row("Chain", " → ".join(chain)))
        try:
            der = bytes(p.certificate)
            fp = hashlib.sha256(der).hexdigest().upper()
            group.add(self._row("SHA-256 fingerprint", ":".join(fp[i:i + 2] for i in range(0, len(fp), 2))))
        except (AttributeError, TypeError):
            pass

    def _lookup(self):
        from . import sitetools
        try:
            w = sitetools.whois(self.host)
        except Exception as e:  # noqa: BLE001
            w = e
        try:
            recs = sitetools.dns(self.host)
        except Exception as e:  # noqa: BLE001
            recs = e
        owners = []
        if isinstance(recs, list):
            for t, v in recs:
                if t in ("A", "AAAA") and len(owners) < 4:
                    o = sitetools.ip_owner(v)
                    if o:
                        owners.append(o)
        GLib.idle_add(self._show, w, recs, owners)

    def _show(self, w, recs, owners):
        from . import sitetools
        self.domain.remove(self._looking)
        if isinstance(w, Exception):
            self.domain.add(self._row("No registration record", str(w)))
        else:
            self.domain.add(self._row("Domain", w["domain"]))
            if w["created"]:
                if (GLib.DateTime.new_now_utc().to_unix() - int(w["created"].timestamp())) < 60 * 86400:
                    warn = self._row("New domain", "This domain is less than two months old. New domains are common "
                                                   "for scams and phishing.")
                    warn.add_css_class("warning")
                    self.domain.add(warn)
                self.domain.add(self._row("Registered", w["created"].strftime("%b %-d, %Y") + " (" + sitetools.age(w["created"]) + ")"))
            if w["expires"]:
                self.domain.add(self._row("Expires", w["expires"].strftime("%b %-d, %Y")))
            if w["registrar"]:
                self.domain.add(self._row("Registrar", w["registrar"]))
            if w["registrant"]:
                self.domain.add(self._row("Registrant", w["registrant"]))
            if w["nameservers"]:
                self.domain.add(self._row("Name servers", ", ".join(w["nameservers"][:6])))
        if isinstance(recs, Exception):
            self.records.add(self._row("DNS lookup failed", str(recs)))
        else:
            for t, v in recs[:40]:
                self.records.add(self._row(t, v))
        for o in owners:
            self.hosts.add(self._row(o["ip"], " · ".join(x for x in (o["owner"] or o["network"], o["country"], o["cidr"]) if x)))
        if not owners:
            self.hosts.add(self._row("Unknown", ""))
        return False


class ClipDialog(Adw.Dialog):
    """Clip the article as Markdown: into your Obsidian vault (or any notes folder), a file, or the clipboard."""

    def __init__(self, app, article):
        super().__init__(title="Clip", content_width=460)
        self.app, self.article = app, article
        s = app.settings
        page = Adw.PreferencesPage()
        tv = Adw.ToolbarView(content=page)
        header = Adw.HeaderBar()
        self.go = Gtk.Button(label="Clip")
        self.go.add_css_class("suggested-action")
        self.go.connect("clicked", self.on_clip)
        header.pack_end(self.go)
        tv.add_top_bar(header)
        self.set_child(tv)

        what = Adw.PreferencesGroup(title=GLib.markup_escape_text(urls.host_of(article.get("url", ""))))
        self.title_row = Adw.EntryRow(title="Title", text=article.get("title") or "")
        self.tags_row = Adw.EntryRow(title="Tags (comma separated)", text=s["clip_tags"])
        what.add(self.title_row)
        what.add(self.tags_row)
        page.add(what)

        where = Adw.PreferencesGroup(title="Clip to")
        self.dest = Adw.ComboRow(title="Destination", model=Gtk.StringList.new(
            ["Obsidian vault / notes folder", "Markdown file…", "Copy as Markdown"]))
        self.dest.set_selected({"folder": 0, "file": 1, "copy": 2}.get(s["clip_destination"], 0))
        where.add(self.dest)
        self.folder_row = Adw.ActionRow(title="Folder", subtitle=GLib.markup_escape_text(s["clip_folder"] or "Not chosen yet"))
        pick = Gtk.Button(label="Choose…", valign=Gtk.Align.CENTER)
        pick.connect("clicked", self.on_pick)
        self.folder_row.add_suffix(pick)
        where.add(self.folder_row)
        sub = Adw.EntryRow(title="Subfolder", text=s["clip_subfolder"])
        sub.connect("changed", lambda r: s.__setitem__("clip_subfolder", r.get_text().strip().strip("/")))
        where.add(sub)
        page.add(where)
        page.add(Adw.PreferencesGroup(description="Clips are Markdown with the title, source and tags at the top. "
                                                  "Pick your Obsidian vault as the folder and they appear there."))

    def on_pick(self, *_):
        dialog = Gtk.FileDialog(title="Choose your Obsidian vault or notes folder", modal=True)

        def picked(d, res):
            try:
                f = d.select_folder_finish(res)
            except GLib.Error:
                return
            if f and f.get_path():
                self.app.settings["clip_folder"] = f.get_path()
                self.folder_row.set_subtitle(GLib.markup_escape_text(f.get_path()))
        dialog.select_folder(self.app.window, None, picked)

    def on_clip(self, *_):
        s = self.app.settings
        tags = [t for t in self.tags_row.get_text().split(",") if t.strip()]
        s["clip_tags"] = self.tags_row.get_text().strip()
        title = self.title_row.get_text().strip() or None
        choice = ["folder", "file", "copy"][self.dest.get_selected()]
        s["clip_destination"] = choice
        if choice == "copy":
            self.app.window.get_clipboard().set(clip.note(self.article, tags, title))
            self.app.window.toast("Copied as Markdown")
            self.close()
        elif choice == "folder":
            if not s["clip_folder"]:
                self.on_pick()
                return
            try:
                path = clip.save_to_folder(s["clip_folder"], self.article, tags, title, s["clip_subfolder"])
                self.app.window.toast("Clipped to " + os.path.basename(os.path.dirname(path)))
                self.close()
            except Exception as e:  # noqa: BLE001
                self.app.window.toast(f"Couldn't save the clip: {e}")
        else:
            dialog = Gtk.FileDialog(title="Save clip", modal=True, initial_name=clip.file_name(title or self.article.get("title")))

            def saved(d, res):
                try:
                    f = d.save_finish(res)
                except GLib.Error:
                    return
                f.replace_contents(clip.note(self.article, tags, title).encode("utf-8"), None, False,
                                   Gio.FileCreateFlags.REPLACE_DESTINATION, None)
                self.app.window.toast("Clip saved")
                self.close()
            dialog.save(self.app.window, None, saved)


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
        self.tab_bar = Adw.TabBar(view=self.tab_view, autohide=False, hexpand=True)

        # The controls: navigation, shield, address bar, search engine and tools.
        self.back = self._button("go-previous-symbolic", "Back", lambda *_: self.view and self.view.go_back())
        self.fwd = self._button("go-next-symbolic", "Forward", lambda *_: self.view and self.view.go_forward())
        self.reload = self._button("view-refresh-symbolic", "Reload", self.on_reload)

        self.shield = Gtk.MenuButton(tooltip_text="Protection for this site")
        self.shield.add_css_class("flat")
        self.shield_label = Gtk.Label(label="0")
        shield_box = Gtk.Box(spacing=4)
        shield_box.append(Gtk.Image.new_from_icon_name("security-high-symbolic"))
        shield_box.append(self.shield_label)
        self.shield.set_child(shield_box)
        self.shield_popover = Gtk.Popover()
        self.shield.set_popover(self.shield_popover)
        self.reader_button = self._button("format-justify-left-symbolic", "Reader view (Ctrl+Alt+R)",
                                          lambda *_: self.app.toggle_reader())
        self.reader_button.add_css_class("flat")
        self.shield_popover.connect("show", lambda *_: self.fill_shield())

        self.entry = Gtk.Entry(hexpand=True, placeholder_text="Search or enter address")
        self.entry.set_input_purpose(Gtk.InputPurpose.URL)
        self.entry.connect("activate", self.on_go)
        self.entry.set_max_width_chars(80)

        menu = Gio.Menu()
        menu.append("New Private Tab", "app.private-tab")
        menu.append("Feeds", "app.feeds")
        menu.append("Add Page to Feeds", "win.follow")
        menu.append("Add to Favorites", "app.favorite")
        menu.append("Keep This Tab", "app.keep")
        menu.append("Hide Distracting Item…", "app.pick")
        menu.append("Show Hidden Items on This Site", "app.unhide")
        menu.append("Reader View", "app.reader")
        menu.append("Clip Page…", "app.clip")
        menu.append("Settings", "app.settings")
        menu.append("About Airlock", "app.about")
        self.menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Menu")
        self.burn_button = self._button("user-trash-full-symbolic", "Burn: close every tab and erase browsing data",
                                        lambda *_: self.app.burn())
        self.newtab_button = self._button("tab-new-symbolic", "New tab", lambda *_: self.new_tab(focus=True))
        self.engine_button = Gtk.MenuButton(tooltip_text="Search engine")
        self.build_engine_menu()

        follow = Gio.SimpleAction.new("follow", None)
        follow.connect("activate", lambda *_: self.follow_current())
        self.add_action(follow)

        self.toolbar_view = None
        self.layout()

    def layout(self):
        """Address bar at the bottom (like Airlock on iPhone, tabs in the title bar) or at the top (classic)."""
        bottom = self.app.settings["bar_at_bottom"]
        if self.toolbar_view:
            # Take the shared widgets out of the old layout so they can be placed in the new one.
            old = self.toolbar_view
            old.set_content(None)
            self._header.set_title_widget(None)
            if self.tab_bar.get_parent() is not None and not self._bottom:
                old.remove(self.tab_bar)
            for box, widgets in self._boxes:
                for w in widgets:
                    box.remove(w)
            self._clamp.set_child(None)
        tv = Adw.ToolbarView()
        header = Adw.HeaderBar()
        controls = Gtk.Box(spacing=6, hexpand=True)
        for w in (self.back, self.fwd, self.reload):
            controls.append(w)
        middle = Gtk.Box(spacing=2, hexpand=True)
        middle.append(self.shield)
        middle.append(self.reader_button)
        middle.append(self.entry)
        clamp = Adw.Clamp(maximum_size=820, child=middle, hexpand=True)
        controls.append(clamp)
        for w in (self.engine_button, self.newtab_button, self.burn_button, self.menu_button):
            controls.append(w)
        if bottom:
            # Title bar: the tabs (COSMIC and GNOME draw the window buttons around them).
            header.set_title_widget(self.tab_bar)
            tv.add_top_bar(header)
            bar = Gtk.Box(css_classes=["toolbar"])
            bar.append(controls)
            tv.add_bottom_bar(bar)
            tv.set_bottom_bar_style(Adw.ToolbarStyle.RAISED)
        else:
            header.set_title_widget(controls)
            tv.add_top_bar(header)
            tv.add_top_bar(self.tab_bar)
        tv.set_content(self.tab_view)
        self._header, self._clamp, self._bottom = header, clamp, bottom
        self._boxes = [(controls, [self.back, self.fwd, self.reload, clamp, self.engine_button, self.newtab_button,
                                   self.burn_button, self.menu_button]), (middle, [self.shield, self.reader_button, self.entry])]
        self.toolbar_view = tv
        self.toasts.set_child(tv)
        if self.get_content() is not self.toasts:
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
        else:
            tab.open_internal("airlock://start")
            if focus:
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

    def close_all(self, keep=False):
        for page in list(self.tabs):
            if keep and page.get_pinned():
                continue
            self.tab_view.close_page(page)

    def toggle_keep(self):
        page = self.tab_view.get_selected_page()
        if not page:
            return
        tab = self.tabs.get(page)
        if tab and tab.private:
            self.toast("Private tabs can't be kept")
            return
        self.tab_view.set_page_pinned(page, not page.get_pinned())
        self.toast("Kept: this tab stays through Burn and reopens next time" if page.get_pinned() else "No longer kept")
        self.app.save_kept()

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
            self.entry.set_text("" if uri.startswith(("about:", "airlock://start")) else uri)
        p = v.get_estimated_load_progress()
        self.entry.set_progress_fraction(p if v.is_loading() and p < 1 else 0)
        n = len(tab.trackers)
        self.shield_label.set_label(str(n))
        off = self.app.shields_off(urls.host_of(uri))
        self.shield.set_tooltip_text("Shields are off for this site" if off else
                                     f"{n} tracker{'s' if n != 1 else ''} blocked on this page")
        if off:
            self.shield.add_css_class("warning")
        else:
            self.shield.remove_css_class("warning")
        self.back.set_sensitive(v.can_go_back())
        self.fwd.set_sensitive(v.can_go_forward())
        self.reload.set_icon_name("process-stop-symbolic" if v.is_loading() else "view-refresh-symbolic")

    def fill_shield(self):
        """The shield's popover: what was blocked here, and switches for this site."""
        tab = self.current
        s = self.app.settings
        uri = tab.view.get_uri() if tab else ""
        host = urls.host_of(uri or "")
        site = urls.site_key(host)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=12, margin_bottom=12,
                      margin_start=12, margin_end=12, width_request=300)
        title = Gtk.Label(label=site or "This page", xalign=0)
        title.add_css_class("title-3")
        box.append(title)
        if not uri.startswith(("http://", "https://")):
            box.append(Gtk.Label(label="Open a website to see what Airlock blocks on it.", xalign=0, wrap=True))
            self.shield_popover.set_child(box)
            return
        info = Gtk.Button(label="Site info: certificate, whois, DNS…")
        info.add_css_class("pill")
        info.add_css_class("suggested-action")
        info.connect("clicked", lambda *_: (self.shield_popover.popdown(), SiteInfo(self, tab).present(self)))
        box.append(info)
        secure = uri.startswith("https://")
        box.append(Gtk.Label(label="Secure connection (HTTPS)" if secure else "Not secure: plain HTTP", xalign=0))

        on = Adw.SwitchRow(title="Shields", subtitle="Ad and tracker blocking on this site", active=not self.app.shields_off(host))
        on.connect("notify::active", lambda r, _: self.app.set_shields(host, r.get_active()))
        mode = Adw.ComboRow(title="Protection", model=Gtk.StringList.new(["Standard", "Strict"]),
                            selected=1 if s["protection"] == "strict" else 0)
        mode.connect("notify::selected", lambda r, _: s.__setitem__("protection", "strict" if r.get_selected() else "standard"))
        ads = Adw.SwitchRow(title="Block ads", active=s["block_ads"])
        ads.connect("notify::active", lambda r, _: s.__setitem__("block_ads", r.get_active()))
        rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        rows.add_css_class("boxed-list")
        for row in (on, mode, ads):
            rows.append(row)
        box.append(rows)

        found = sorted(tab.trackers)
        head = Gtk.Label(xalign=0, label=f"{len(found)} tracker{'s' if len(found) != 1 else ''} on this page"
                         + ("" if self.app.shields_off(host) else
                            (" (blocked)" if s["protection"] == "strict" else " (cookies blocked)")))
        head.add_css_class("heading")
        box.append(head)
        if found:
            lst = Gtk.Label(label="\n".join(found), xalign=0, yalign=0, selectable=True)
            lst.add_css_class("dim-label")
            scroller = Gtk.ScrolledWindow(child=lst, propagate_natural_height=True, max_content_height=140,
                                          hscrollbar_policy=Gtk.PolicyType.NEVER)
            box.append(scroller)
        note = Gtk.Label(label="Ads, cookie banners and EasyPrivacy trackers are blocked too, before they load.",
                         xalign=0, wrap=True, max_width_chars=40)
        note.add_css_class("caption")
        note.add_css_class("dim-label")
        box.append(note)
        self.shield_popover.set_child(box)

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
        self.articles = {}           # Reader view articles by id (kept in memory only)
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
        act("reader", lambda *_: self.toggle_reader())
        act("clip", lambda *_: self.clip_page())
        act("favorite", lambda *_: self.add_favorite())
        act("keep", lambda *_: self.window.toggle_keep())
        act("pick", lambda *_: self.window.current and self.window.current.pick())
        act("unhide", lambda *_: self.unhide())
        act("settings", lambda *_: self.show_settings())
        act("about", lambda *_: self.show_about())
        act("engine", self.on_engine, GLib.VariantType.new("s"))
        for action, keys in {"app.new-tab": ["<Control>t"], "app.private-tab": ["<Control><Shift>n"],
                             "app.close-tab": ["<Control>w"], "app.focus-address": ["<Control>l", "F6"],
                             "app.reload": ["<Control>r", "F5"], "app.back": ["<Alt>Left"],
                             "app.forward": ["<Alt>Right"], "app.burn": ["<Control><Shift>Delete"],
                             "app.reader": ["<Control><Alt>r"], "app.clip": ["<Control><Shift>s"],
                             "app.favorite": ["<Control>d"]}.items():
            self.set_accels_for_action(action, keys)
        self.settings.on_change(self.on_setting_changed)

    def do_activate(self):
        if not self.window:
            self.window = Window(self)
            for url in self.settings["kept_tabs"]:
                if urlsplit(url).scheme in ("http", "https"):
                    t = self.window.new_tab(url=url)
                    self.window.tab_view.set_page_pinned(self.window.tab_view.get_selected_page(), True)
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
        if key == "bar_at_bottom" and self.window:
            self.window.layout()
        if key in ("protection", "block_ads", "easyprivacy", "hide_cookie_banners", "gpc", "hidden") and self.window:
            for tab in self.window.tabs.values():
                tab.apply_rules()
        if key == "generic_ua" and self.window:
            for tab in self.window.tabs.values():
                engine.apply_user_agent(tab.view.get_settings(), self.settings)

    def shields_off(self, host):
        site = urls.site_key(host or "")
        return bool(site) and site in self.settings["shields_off"]

    def set_shields(self, host, on):
        site = urls.site_key(host or "")
        if not site:
            return
        sites = [x for x in self.settings["shields_off"] if x != site]
        if not on:
            sites.append(site)
        self.settings["shields_off"] = sorted(sites)
        for tab in self.window.tabs.values():
            tab.apply_rules()
            if urls.site_key(urls.host_of(tab.view.get_uri() or "")) == site:
                tab.view.reload()
        self.window.sync_bar()

    def on_engine(self, action, value):
        self.settings["engine"] = value.get_string()

    def unhide(self):
        tab = self.window.current
        site = urls.site_key(urls.host_of(tab.view.get_uri() or "")) if tab else ""
        if site and site in self.settings["hidden"]:
            hidden = dict(self.settings["hidden"])
            hidden.pop(site)
            self.settings["hidden"] = hidden
            tab.view.reload()
            self.window.toast(f"Everything on {site} is showing again")
        else:
            self.window.toast("Nothing is hidden on this site")

    def add_favorite(self):
        tab = self.window.current
        uri = tab.view.get_uri() if tab else ""
        if not uri.startswith(("https://", "http://")):
            self.window.toast("Open a website first")
            return
        if any(f.get("url") == uri for f in self.settings["favorites"]):
            self.window.toast("Already in Favorites")
            return
        title = (tab.view.get_title() or urls.site_key(urls.host_of(uri)))[:60]
        self.settings["favorites"] = self.settings["favorites"] + [{"title": title, "url": uri}]
        self.window.toast(f"Added to Favorites: {title}")

    # Reader view and clipping

    def extract(self, tab, done):
        """Finds the article on the tab's page (Readability, in Airlock's private script world)."""
        if not hasattr(self, "_extract_js"):
            self._extract_js = clip.extract_script()
        uri = tab.view.get_uri() or ""
        if not uri.startswith(("http://", "https://")):
            done(None, "Open an article first")
            return

        def finished(view, res):
            try:
                out = json.loads(view.evaluate_javascript_finish(res).to_string())
            except Exception as e:  # noqa: BLE001
                done(None, str(e))
                return
            if not out.get("ok"):
                done(None, "Airlock couldn't find an article on this page")
                return
            done(out, None)
        tab.view.evaluate_javascript(self._extract_js, -1, engine.SCANNER_WORLD, None, None, finished)

    def toggle_reader(self):
        tab = self.window.current
        if not tab:
            return
        if (tab.view.get_uri() or "").startswith("airlock://reader"):
            tab.view.go_back()
            return

        def show(article, error):
            if error:
                self.window.toast(error)
                return
            key = str(len(self.articles) + 1)
            self.articles[key] = article
            tab.open_internal("airlock://reader?id=" + key)
        self.extract(tab, show)

    def clip_page(self):
        tab = self.window.current
        if not tab:
            return
        uri = tab.view.get_uri() or ""
        if uri.startswith("airlock://reader"):
            key = parse_qs(urlsplit(uri).query).get("id", [""])[0]
            if key in self.articles:
                ClipDialog(self, self.articles[key]).present(self.window)
                return

        def show(article, error):
            if error:
                self.window.toast(error)
                return
            ClipDialog(self, article).present(self.window)
        self.extract(tab, show)

    # Burn

    def burn(self, then=None):
        w = self.window
        w.close_all(keep=True)

        def done():
            w.toast("Burned: tabs closed and browsing data erased")
            if then:
                then()
        self.sessions.clear_normal(done)

    def save_kept(self):
        if not self.window:
            return
        kept = []
        for page, tab in self.window.tabs.items():
            uri = tab.view.get_uri() or ""
            if page.get_pinned() and uri.startswith(("http://", "https://")):
                kept.append(uri)
        self.settings["kept_tabs"] = kept

    def on_shutdown(self, *_):
        self.save_kept()
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

    def import_opml(self):
        dialog = Gtk.FileDialog(title="Import feeds (OPML)", modal=True)

        def picked(d, res):
            try:
                f = d.open_finish(res)
                ok, data, _ = f.load_contents(None)
                entries = feeds.FeedStore.parse_opml(bytes(data)[:5_000_000])
            except Exception as e:  # noqa: BLE001
                self.window.toast(f"Couldn't read that file: {e}")
                return
            self.window.toast(f"Importing {len(entries)} feeds…")
            import threading

            def work():
                added = 0
                for url, folder in entries:
                    try:
                        self.feeds.follow(url)
                        if folder:
                            for fd in self.feeds.feeds:
                                if fd["url"].rstrip("/").lower() in (url.rstrip("/").lower(), urls.upgrade_to_https(url).rstrip("/").lower()):
                                    fd["folder"] = folder
                        added += 1
                    except Exception:  # noqa: BLE001
                        pass
                self.feeds.save()
                GLib.idle_add(lambda: (self.window.toast(f"Imported {added} of {len(entries)} feeds"),
                                       self.reload_feed_tabs(), False)[-1])
            threading.Thread(target=work, daemon=True).start()
        dialog.open(self.window, None, picked)

    def reload_feed_tabs(self):
        for tab in self.window.tabs.values():
            if (tab.view.get_uri() or "").startswith("airlock://feeds"):
                tab.open_internal("airlock://feeds?scope=" + GLib.Uri.escape_string(getattr(self, "feed_scope", "today"), None, False))

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
            return self._redirect(request, "airlock://feeds?scope=" + GLib.Uri.escape_string(q.get("scope", ["today"])[0], None, False))
        if route == "reader" and q.get("id"):
            article = self.articles.get(q["id"][0])
            if article:
                return self._finish(request, clip.reader_page(article))
        if route == "mode" and q.get("m"):
            m = q["m"][0]
            if m == "hardened":
                for k, v in store.HARDENED.items():
                    self.settings[k] = v
            elif m in ("standard", "strict"):
                if store.is_hardened(self.settings):
                    self.settings["clear_on_quit"] = False
                    self.settings["generic_ua"] = False
                self.settings["protection"] = m
            return self._redirect(request, "airlock://start")
        if route == "favorites/remove" and q.get("url"):
            self.settings["favorites"] = [f for f in self.settings["favorites"] if f.get("url") != q["url"][0]]
            return self._redirect(request, "airlock://start")
        if route == "start":
            return self._finish(request, start_page(self.settings, self.window.current))
        if route == "logo.png":
            with open(os.path.join(engine.DATA, "logo.png"), "rb") as f:
                data = GLib.Bytes.new(f.read())
            request.finish(Gio.MemoryInputStream.new_from_bytes(data), data.get_size(), "image/png")
            return
        if route == "search" and q.get("q"):
            target = urls.url_from_input(q["q"][0], self.settings["engine"])
            tab = self.window.current
            if target and tab:
                GLib.idle_add(lambda: (tab.load(target, typed=True), False)[-1])
            return self._finish(request, page_html("", ""))
        if route == "feeds":
            msg, self.feed_message = self.feed_message, ""
            self.feed_scope = q.get("scope", [getattr(self, "feed_scope", "today")])[0]
            return self._finish(request, feeds.render(self.feeds, msg, self.feed_scope))
        back = "airlock://feeds?scope=" + GLib.Uri.escape_string(q.get("scope", [getattr(self, "feed_scope", "today")])[0], None, False)
        if route == "feeds/open" and q.get("id"):
            item = next((i for i in self.feeds.items if i["id"] == q["id"][0]), None)
            if item and urlsplit(item["link"]).scheme in ("http", "https"):
                self.feeds.read.add(item["id"])
                self.feeds.save()
                return self._redirect(request, html.escape(item["link"]))
            return self._redirect(request, back)
        if route == "feeds/later" and q.get("id"):
            iid = q["id"][0]
            if iid in self.feeds.later:
                self.feeds.later.discard(iid)
            else:
                self.feeds.later.add(iid)
            self.feeds.save()
            return self._redirect(request, back)
        if route == "feeds/readall":
            self.feeds.read.update(i["id"] for i in self.feeds.scoped(q.get("scope", ["all"])[0]))
            self.feeds.save()
            return self._redirect(request, back)
        if route == "feeds/folder" and q.get("id"):
            self.feeds.set_folder(q["id"][0], q.get("name", [""])[0])
            return self._redirect(request, back)
        if route == "feeds/export":
            folder = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD) or store.DATA_DIR
            path = os.path.join(folder, "Airlock feeds.opml")
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.feeds.opml())
            self.feed_message = f"Saved {path}"
            return self._redirect(request, back)
        if route == "feeds/import":
            GLib.idle_add(lambda: (self.import_opml(), False)[-1])
            return self._redirect(request, back)
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
                           ("clear_on_quit", "Erase browsing data when Airlock quits"),
                           ("generic_ua", "Common user agent (look like Safari on a Mac)")):
            row = Adw.SwitchRow(title=title, active=s[key])
            row.connect("notify::active", lambda r, _, k=key: s.__setitem__(k, r.get_active()))
            prot.add(row)
        page.add(prot)

        look = Adw.PreferencesGroup(title="Layout")
        bottom = Adw.SwitchRow(title="Address bar at the bottom", subtitle="Tabs move up into the title bar",
                               active=s["bar_at_bottom"])
        bottom.connect("notify::active", lambda r, _: s.__setitem__("bar_at_bottom", r.get_active()))
        look.add(bottom)
        page.add(look)
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
