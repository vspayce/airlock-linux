"""Feeds: RSS / Atom / JSON Feed, plus pages with no feed (their headlines are read instead).

Fetched by Airlock itself over HTTPS with no cookies, public addresses only, at most 5 MB.
"""
import html
import json
import re
import threading
import time
import uuid
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

from . import netguard, store, urls

MAX_BYTES = 5 * 1024 * 1024
KEEP_PER_FEED = 60


class FeedError(Exception):
    pass


class _NoRedirectIntoNetwork(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        p = urlsplit(newurl)
        if p.scheme != "https" or not netguard.resolves_public(p.hostname or ""):
            raise FeedError("Refused a redirect away from public HTTPS.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_NoRedirectIntoNetwork())


def fetch(url, accept=None):
    url = urls.upgrade_to_https(url)
    p = urlsplit(url)
    if p.scheme != "https" or not netguard.resolves_public(p.hostname or ""):
        raise FeedError("Airlock only fetches feeds over HTTPS from public websites.")
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15",
        "Accept": accept or "application/rss+xml, application/atom+xml, application/feed+json, text/html;q=0.8, */*;q=0.5",
    })
    with _opener.open(req, timeout=20) as r:
        data = r.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise FeedError("That feed is too large (over 5 MB).")
        return data, r.geturl()


# MARK: Parsing

def _text(el):
    return "".join(el.itertext()).strip() if el is not None else ""


def _local(tag):
    return tag.rsplit("}", 1)[-1].lower()


def _date(s):
    s = (s or "").strip()
    if not s:
        return None
    try:
        return parsedate_to_datetime(s).timestamp()
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def plain(text, limit=220):
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", text or "")
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def parse_feed(data, base):
    """Returns (title, items) or None if this isn't a feed."""
    head = data[:200].lstrip()
    if head.startswith(b"{"):
        try:
            j = json.loads(data)
        except ValueError:
            return None
        if "items" not in j:
            return None
        items = [{"id": str(i.get("id") or i.get("url")), "title": plain(i.get("title") or "", 300),
                  "link": urljoin(base, i.get("url") or ""), "date": _date(i.get("date_published")),
                  "summary": plain(i.get("content_html") or i.get("content_text") or i.get("summary") or "")}
                 for i in j.get("items", [])[:200]]
        return j.get("title") or "", items
    if b"<!doctype html" in head.lower() or b"<html" in head.lower():
        return None
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None
    kind = _local(root.tag)
    items, title = [], ""
    if kind in ("rss", "rdf"):
        channel = next((c for c in root if _local(c.tag) == "channel"), root)
        title = _text(next((c for c in channel if _local(c.tag) == "title"), None))
        nodes = [n for n in root.iter() if _local(n.tag) == "item"]
        for n in nodes[:200]:
            f = {_local(c.tag): c for c in n}
            link = _text(f.get("link"))
            items.append({"id": _text(f.get("guid")) or link, "title": plain(_text(f.get("title")), 300),
                          "link": urljoin(base, link), "date": _date(_text(f.get("pubdate")) or _text(f.get("date"))),
                          "summary": plain(_text(f.get("description")) or _text(f.get("encoded")))})
    elif kind == "feed":
        title = _text(next((c for c in root if _local(c.tag) == "title"), None))
        for n in [c for c in root if _local(c.tag) == "entry"][:200]:
            f = {}
            link = ""
            for c in n:
                t = _local(c.tag)
                f.setdefault(t, c)
                if t == "link" and c.get("rel", "alternate") == "alternate" and not link:
                    link = c.get("href", "")
            items.append({"id": _text(f.get("id")) or link, "title": plain(_text(f.get("title")), 300),
                          "link": urljoin(base, link), "date": _date(_text(f.get("updated")) or _text(f.get("published"))),
                          "summary": plain(_text(f.get("summary")) or _text(f.get("content")))})
    else:
        return None
    items = [i for i in items if urlsplit(i["link"]).scheme in ("http", "https")]
    return title, items


def discover(data, base):
    text = data[:400_000].decode("utf-8", "replace")
    for tag in re.findall(r"(?is)<link\b[^>]*>", text):
        low = tag.lower()
        if "alternate" in low and any(t in low for t in ("application/rss+xml", "application/atom+xml", "application/feed+json")):
            m = re.search(r"""(?i)href\s*=\s*["']([^"']+)["']""", tag)
            if m:
                return urljoin(base, html.unescape(m.group(1)))
    return None


# MARK: Pages without a feed

_SKIP = ("/tag/", "/tags/", "/category/", "/categories/", "/author/", "/authors/", "/login", "/signin",
         "/sign-in", "/signup", "/register", "/subscribe", "/account", "/search", "/about", "/contact",
         "/privacy", "/terms", "/cookie", "/advertis", "/careers", "/jobs", "/page/", "/feed", "/rss")


def _page_item(href, text, base, min_len):
    href = html.unescape(href).strip()
    if href.startswith("#"):
        return None
    url = urljoin(base, href)
    p, b = urlsplit(url), urlsplit(base)
    if p.scheme not in ("http", "https") or not p.hostname or not b.hostname:
        return None
    site = urls.site_key(b.hostname)
    host = p.hostname.lower()
    if host != site and not host.endswith("." + site):
        return None
    path = p.path.lower()
    if len(path) <= 1 or p.path == b.path or any(path.startswith(s) or (s + "/") in path for s in _SKIP):
        return None
    title = plain(text, 300)
    if len(title) < min_len or len(title.split()) < 2:
        return None
    m = re.search(r"(20\d{2})[/-](\d{1,2})[/-](\d{1,2})(?:/|-|$)", path)
    date = None
    if m:
        try:
            date = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), 12, tzinfo=timezone.utc).timestamp()
        except ValueError:
            pass
    clean = url.split("#", 1)[0]
    return {"id": "page:" + path, "title": title, "link": urls.upgrade_to_https(clean), "date": date, "summary": ""}


def parse_page(data, base):
    text = data[:700_000].decode("utf-8", "replace")
    for tag in ("script", "style", "noscript", "svg", "template", "nav", "footer", "header", "aside"):
        text = re.sub(rf"(?is)<{tag}\b.*?</{tag}>", " ", text)
    items, seen = [], set()

    def add(item):
        if item and item["id"] not in seen:
            seen.add(item["id"])
            items.append(item)

    for m in re.finditer(r"(?is)<h[1-4]\b[^>]*>(.{0,1200}?)</h[1-4]>", text):
        a = re.search(r"""(?is)<a\b[^>]*href\s*=\s*["']([^"']+)["'][^>]*>(.*?)</a>""", m.group(1))
        if a:
            add(_page_item(a.group(1), a.group(2), base, 8))
    for m in re.finditer(r"""(?is)<a\b[^>]*href\s*=\s*["']([^"']+)["'][^>]*>\s*(?:<[^>]+>\s*)*<h[1-4]\b[^>]*>(.{0,600}?)</h[1-4]>""", text):
        add(_page_item(m.group(1), m.group(2), base, 8))
    if len(items) < 3:
        for m in re.finditer(r"""(?is)<a\b[^>]*href\s*=\s*["']([^"']+)["'][^>]*>(.{0,800}?)</a>""", text):
            item = _page_item(m.group(1), m.group(2), base, 24)
            if item and ("-" in urlsplit(item["link"]).path or any(c.isdigit() for c in urlsplit(item["link"]).path)):
                add(item)
    title = ""
    m = re.search(r"""(?is)<meta\b[^>]*property\s*=\s*["']og:site_name["'][^>]*content\s*=\s*["']([^"']+)["']""", data[:200_000].decode("utf-8", "replace"))
    if m:
        title = plain(m.group(1), 80)
    if not title:
        m = re.search(r"(?is)<title\b[^>]*>(.{0,400}?)</title>", data[:200_000].decode("utf-8", "replace"))
        title = plain(m.group(1), 80) if m else ""
    return title or (urlsplit(base).hostname or "Web page"), items[:40]


# MARK: Store

class FeedStore:
    def __init__(self):
        saved = store.read_json("feeds.json", {})
        self.feeds = saved.get("feeds", [])
        self.items = saved.get("items", [])
        self.read = set(saved.get("read", []))
        self.listeners = []
        self.busy = False

    def save(self):
        store.write_json("feeds.json", {"feeds": self.feeds, "items": self.items, "read": sorted(self.read)[-5000:]})

    def changed(self):
        for fn in list(self.listeners):
            fn()

    def is_following(self, url):
        key = url.rstrip("/").lower()
        return any(f["url"].rstrip("/").lower() == key for f in self.feeds)

    def _load(self, feed):
        data, final = fetch(feed["url"])
        if feed.get("watches_page"):
            title, items = parse_page(data, final)
            if len(items) < 2:
                raise FeedError("Airlock couldn't find headlines on that page.")
            return title, items
        parsed = parse_feed(data, final)
        if not parsed:
            raise FeedError("No feed found at that address.")
        return parsed

    def follow(self, url):
        """Follows a feed, finds one on a page, or watches the page's headlines. Returns the title. Runs off the main thread."""
        url = urls.upgrade_to_https(url)
        data, final = fetch(url)
        parsed = parse_feed(data, final)
        feed_url, watches = final, False
        if parsed is None:
            found = discover(data, final)
            if found:
                feed_url = urls.upgrade_to_https(found)
                fdata, ffinal = fetch(feed_url)
                parsed = parse_feed(fdata, ffinal)
        if parsed is None:
            title, items = parse_page(data, final)
            if len(items) < 2:
                raise FeedError("No feed or headlines found at that address.")
            parsed, feed_url, watches = (title, items), final, True
        title, items = parsed
        if self.is_following(feed_url):
            return title
        feed = {"id": str(uuid.uuid4()), "url": feed_url, "title": title or urlsplit(feed_url).hostname,
                "watches_page": watches, "added": time.time()}
        self.feeds.append(feed)
        self._merge(feed, items)
        self.save()
        return feed["title"]

    def unfollow(self, feed_id):
        self.feeds = [f for f in self.feeds if f["id"] != feed_id]
        self.items = [i for i in self.items if i["feed"] != feed_id]
        self.save()

    def _merge(self, feed, items):
        existing = {i["id"]: i for i in self.items if i["feed"] == feed["id"]}
        now = time.time()
        for n, it in enumerate(items):
            iid = feed["id"] + "|" + (it["id"] or it["link"])
            old = existing.get(iid)
            # Undated headlines keep the time they were first seen (newest first, a second apart).
            date = it["date"] or (old["date"] if old else now - n)
            existing[iid] = {"id": iid, "feed": feed["id"], "title": it["title"] or it["link"], "link": it["link"],
                             "date": date, "summary": it["summary"]}
        mine = sorted(existing.values(), key=lambda i: -i["date"])[:KEEP_PER_FEED]
        self.items = [i for i in self.items if i["feed"] != feed["id"]] + mine

    def refresh(self, done=None):
        if self.busy:
            return
        self.busy = True

        def work():
            for feed in list(self.feeds):
                try:
                    _, items = self._load(feed)
                    self._merge(feed, items)
                    feed.pop("error", None)
                except Exception as e:  # one broken feed shouldn't stop the rest
                    feed["error"] = str(e)
            self.save()
            self.busy = False
            from gi.repository import GLib
            GLib.idle_add(lambda: (self.changed(), done and done(), False)[-1])

        threading.Thread(target=work, daemon=True).start()


def render(feedstore, message=""):
    """The Feeds page (airlock://feeds). Everything from feeds is escaped; nothing from them runs."""
    e = html.escape
    names = {f["id"]: f["title"] for f in feedstore.feeds}
    rows = []
    for it in sorted(feedstore.items, key=lambda i: -i["date"])[:300]:
        when = datetime.fromtimestamp(it["date"]).strftime("%b %-d, %-I:%M %p")
        cls = "read" if it["id"] in feedstore.read else ""
        rows.append(f'<li class="{cls}"><a href="{e(it["link"])}">{e(it["title"])}</a>'
                    f'<div class="meta">{e(names.get(it["feed"], ""))} · {e(when)}</div>'
                    + (f'<p>{e(it["summary"])}</p>' if it["summary"] else "") + "</li>")
    sources = "".join(
        f'<li>{e(f["title"])}' + (' <span class="tag">page</span>' if f.get("watches_page") else "")
        + (f' <span class="err">{e(f["error"])}</span>' if f.get("error") else "")
        + f' <a class="x" href="airlock://feeds/unfollow?id={e(f["id"])}">Unfollow</a></li>'
        for f in feedstore.feeds)
    empty = "" if rows else '<p class="empty">Follow a site to see its latest articles here. Sites without a feed work too: Airlock reads their headlines.</p>'
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Feeds</title>
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; form-action airlock:">
<style>
:root {{ color-scheme: light dark; --accent: #33a3a3; }}
body {{ font: 15px/1.5 system-ui, sans-serif; max-width: 760px; margin: 0 auto; padding: 24px 16px 64px; }}
h1 {{ font-size: 26px; margin: 0 0 12px; }}
form {{ display: flex; gap: 8px; margin: 0 0 8px; }}
input {{ flex: 1; font: inherit; padding: 8px 12px; border-radius: 10px; border: 1px solid #8884; background: transparent; color: inherit; }}
button, .btn {{ font: inherit; padding: 8px 14px; border-radius: 10px; border: 0; background: var(--accent); color: #fff; text-decoration: none; }}
ul {{ list-style: none; padding: 0; margin: 0; }}
.items li {{ padding: 12px 0; border-bottom: 1px solid #8883; }}
.items a {{ color: inherit; text-decoration: none; font-weight: 600; }}
.items li.read a {{ font-weight: 400; opacity: .7; }}
.meta {{ font-size: 13px; opacity: .65; }} p {{ margin: 4px 0 0; font-size: 14px; opacity: .85; }}
.msg {{ color: var(--accent); margin: 4px 0 12px; }} .empty {{ opacity: .7; }}
details {{ margin: 0 0 16px; font-size: 14px; }} .x {{ color: #c55; margin-left: 8px; }}
.tag {{ font-size: 11px; border: 1px solid #8886; border-radius: 6px; padding: 0 5px; }} .err {{ color: #c90; font-size: 12px; }}
.bar {{ display: flex; gap: 12px; align-items: center; margin-bottom: 12px; }}
</style></head><body>
<div class="bar"><h1>Feeds</h1><a class="btn" href="airlock://feeds/refresh">Refresh</a></div>
<form action="airlock://feeds/add" method="get"><input name="url" placeholder="Site or feed address" autocomplete="off"><button>Follow</button></form>
{f'<div class="msg">{e(message)}</div>' if message else ""}
<details><summary>Sources ({len(feedstore.feeds)})</summary><ul>{sources}</ul></details>
{empty}<ul class="items">{"".join(rows)}</ul></body></html>"""
