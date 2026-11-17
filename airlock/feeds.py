"""Feeds: RSS / Atom / JSON Feed, plus pages with no feed (their headlines are read instead).

Fetched by Airlock itself over HTTPS with no cookies, public addresses only, at most 5 MB.
"""
import html
import json
import re
import threading
import time
import uuid
import urllib.parse
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


def _extras(node, raw_html, base):
    """Thumbnail and reading time for an item (Feedly-style rows)."""
    image = None
    if node is not None:
        for c in node.iter():
            t = _local(c.tag)
            url = c.get("url") or c.get("href") or ""
            kind = (c.get("type") or c.get("medium") or "").lower()
            if t == "thumbnail" and url:
                image = url
                break
            if t in ("content", "enclosure", "link") and url and ("image" in kind) and not image:
                image = url
    if not image and raw_html:
        m = re.search(r"""(?is)<img\b[^>]*\bsrc\s*=\s*["']([^"']+)["']""", raw_html)
        if m:
            image = html.unescape(m.group(1))
    if image:
        image = urljoin(base, image)
        if urlsplit(image).scheme != "https":
            image = None
    words = len(plain(raw_html or "", 1_000_000).split())
    return {"image": image, "minutes": max(1, round(words / 230)) if words > 120 else None}


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
        items = []
        for i in j.get("items", [])[:200]:
            body = i.get("content_html") or i.get("content_text") or i.get("summary") or ""
            item = {"id": str(i.get("id") or i.get("url")), "title": plain(i.get("title") or "", 300),
                    "link": urljoin(base, i.get("url") or ""), "date": _date(i.get("date_published")),
                    "summary": plain(body)}
            item.update(_extras(None, body, base))
            if not item["image"] and isinstance(i.get("image"), str) and i["image"].startswith("https://"):
                item["image"] = i["image"]
            items.append(item)
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
            body = _text(f.get("encoded")) or _text(f.get("description"))
            item = {"id": _text(f.get("guid")) or link, "title": plain(_text(f.get("title")), 300),
                    "link": urljoin(base, link), "date": _date(_text(f.get("pubdate")) or _text(f.get("date"))),
                    "summary": plain(_text(f.get("description")) or _text(f.get("encoded")))}
            item.update(_extras(n, body, base))
            items.append(item)
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
            body = _text(f.get("content")) or _text(f.get("summary"))
            item = {"id": _text(f.get("id")) or link, "title": plain(_text(f.get("title")), 300),
                    "link": urljoin(base, link), "date": _date(_text(f.get("updated")) or _text(f.get("published"))),
                    "summary": plain(_text(f.get("summary")) or _text(f.get("content")))}
            item.update(_extras(n, body, base))
            items.append(item)
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
        self.later = set(saved.get("later", []))
        self.listeners = []
        self.busy = False

    def save(self):
        store.write_json("feeds.json", {"feeds": self.feeds, "items": self.items, "read": sorted(self.read)[-5000:],
                                        "later": sorted(self.later)})

    def set_folder(self, feed_id, name):
        for f in self.feeds:
            if f["id"] == feed_id:
                f["folder"] = name.strip()[:40] or None
        self.save()

    def scoped(self, scope):
        names = {f["id"]: f for f in self.feeds}
        items = sorted(self.items, key=lambda i: -i["date"])
        if scope == "today":
            cutoff = time.time() - 86400
            return [i for i in items if i["date"] >= cutoff]
        if scope == "later":
            return [i for i in items if i["id"] in self.later]
        if scope.startswith("folder:"):
            return [i for i in items if (names.get(i["feed"]) or {}).get("folder") == scope[7:]]
        if scope.startswith("feed:"):
            return [i for i in items if i["feed"] == scope[5:]]
        return items

    def folders(self):
        return sorted({f["folder"] for f in self.feeds if f.get("folder")})

    def opml(self):
        e = html.escape
        groups = {}
        for f in self.feeds:
            groups.setdefault(f.get("folder") or "", []).append(f)
        body = []
        for folder, fs in sorted(groups.items()):
            rows = "".join(f'<outline type="rss" text="{e(f["title"])}" title="{e(f["title"])}" xmlUrl="{e(f["url"])}"/>'
                           for f in fs if not f.get("watches_page"))
            body.append(f'<outline text="{e(folder)}">{rows}</outline>' if folder else rows)
        return ('<?xml version="1.0" encoding="UTF-8"?>\n<opml version="2.0"><head><title>Airlock feeds</title></head><body>'
                + "".join(body) + "</body></opml>\n")

    @staticmethod
    def parse_opml(data):
        """[(url, folder)] from an OPML file."""
        out = []
        root = ET.fromstring(data)

        def walk(node, folder):
            for o in node:
                if _local(o.tag) != "outline":
                    continue
                url = o.get("xmlUrl")
                if url and urlsplit(url).scheme in ("http", "https"):
                    out.append((url, folder))
                else:
                    walk(o, (o.get("text") or o.get("title") or "")[:40] or folder)
        body = next((c for c in root if _local(c.tag) == "body"), root)
        walk(body, None)
        return out[:500]

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
                             "date": date, "summary": it["summary"], "image": it.get("image"),
                             "minutes": it.get("minutes")}
        # Saved-for-later items are kept even past the per-feed limit.
        ranked = sorted(existing.values(), key=lambda i: -i["date"])
        mine = ranked[:KEEP_PER_FEED] + [i for i in ranked[KEEP_PER_FEED:] if i["id"] in self.later]
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


def _when(ts):
    d = datetime.fromtimestamp(ts)
    age = time.time() - ts
    if age < 3600:
        return f"{max(1, int(age // 60))}m"
    if age < 86400:
        return f"{int(age // 3600)}h"
    return d.strftime("%b %-d")


def _day(ts):
    d = datetime.fromtimestamp(ts).date()
    today = datetime.now().date()
    if d == today:
        return "Today"
    if (today - d).days == 1:
        return "Yesterday"
    return d.strftime("%A, %B %-d") if (today - d).days < 7 else d.strftime("%B %-d")


def render(feedstore, message="", scope="today"):
    """The Feeds page (airlock://feeds): Feedly-style. Everything from feeds is escaped; nothing from them runs."""
    e = html.escape
    q = lambda v: e(urllib.parse.quote(v, safe=""))  # noqa: E731
    feeds_by_id = {f["id"]: f for f in feedstore.feeds}
    unread = lambda items: sum(1 for i in items if i["id"] not in feedstore.read)  # noqa: E731
    chips = [("today", "Today"), ("all", "All"), ("later", "Read Later")] + \
            [("folder:" + f, f) for f in feedstore.folders()]
    bar = "".join(
        f'<a class="chip{" on" if key == scope else ""}" href="airlock://feeds?scope={q(key)}">{e(name)}'
        + (f' <span class="n">{n}</span>' if (n := unread(feedstore.scoped(key))) and key != "later" else "") + "</a>"
        for key, name in chips)
    title = dict(chips).get(scope) or (feeds_by_id.get(scope[5:], {}).get("title") if scope.startswith("feed:") else "Feeds")
    items = feedstore.scoped(scope)[:400]
    rows, last_day = [], None
    for it in items:
        day = _day(it["date"])
        if day != last_day:
            rows.append(f'<h3 class="day">{e(day)}</h3>')
            last_day = day
        src = feeds_by_id.get(it["feed"], {}).get("title", "")
        meta = " / ".join(x for x in (src, _when(it["date"]), f'{it["minutes"]}min' if it.get("minutes") else "") if x)
        saved = it["id"] in feedstore.later
        thumb = f'<img class="th" src="{e(it["image"])}" alt="" loading="lazy">' if it.get("image") else ""
        rows.append(
            f'<div class="row{" read" if it["id"] in feedstore.read else ""}">'
            f'<a class="main" href="airlock://feeds/open?id={q(it["id"])}"><div class="txt"><div class="t">{e(it["title"])}</div>'
            f'<div class="m">{e(meta)}</div>' + (f'<div class="s">{e(it["summary"])}</div>' if it.get("summary") else "")
            + f'</div>{thumb}</a>'
            f'<a class="save{" on" if saved else ""}" title="Read later" href="airlock://feeds/later?id={q(it["id"])}&scope={q(scope)}">'
            f'{"★" if saved else "☆"}</a></div>')
    if not feedstore.feeds:
        rows.append('<p class="empty">Follow a site to see its latest articles here. Sites without a feed work too: '
                    'Airlock reads their headlines.</p>')
    elif not items:
        rows.append('<p class="empty">' + ("Nothing saved for later yet. Tap ☆ on an article." if scope == "later"
                                           else "No new articles here.") + "</p>")
    folder_opts = "".join(f'<option value="{e(f)}">' for f in feedstore.folders())
    sources = "".join(
        f'<li><a href="airlock://feeds?scope=feed:{q(f["id"])}">{e(f["title"])}</a>'
        + (' <span class="tag">page</span>' if f.get("watches_page") else "")
        + (f' <span class="err">{e(f["error"])}</span>' if f.get("error") else "")
        + f'<form action="airlock://feeds/folder" method="get"><input type="hidden" name="id" value="{e(f["id"])}">'
          f'<input name="name" list="folders" placeholder="Folder" value="{e(f.get("folder") or "")}"><button>Move</button></form>'
        + f'<a class="x" href="airlock://feeds/unfollow?id={q(f["id"])}">Unfollow</a></li>'
        for f in sorted(feedstore.feeds, key=lambda f: ((f.get("folder") or "~"), f["title"].lower())))
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Feeds</title>
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src https:; style-src 'unsafe-inline'; form-action airlock:">
<style>
:root {{ color-scheme: light dark; --accent: #2a8f8f; --line: #8883; }}
@media (prefers-color-scheme: dark) {{ :root {{ --accent: #62d6d6; }} body {{ background: #121722; color: #e2e7ee; }} }}
body {{ font: 15px/1.45 system-ui, sans-serif; margin: 0; }}
.top {{ position: sticky; top: 0; z-index: 2; background: Canvas; border-bottom: 1px solid var(--line); }}
@media (prefers-color-scheme: dark) {{ .top {{ background: #121722; }} }}
.bar {{ display: flex; gap: 8px; overflow-x: auto; padding: 10px 16px; scrollbar-width: none; max-width: 820px; margin: 0 auto; }}
.chip {{ white-space: nowrap; padding: 5px 12px; border-radius: 999px; background: #8881; color: inherit; text-decoration: none; font-weight: 600; font-size: 14px; }}
.chip.on {{ background: var(--accent); color: #fff; }} @media (prefers-color-scheme: dark) {{ .chip.on {{ color: #0d1a1f; }} }}
.n {{ opacity: .7; font-weight: 500; }}
main {{ max-width: 760px; margin: 0 auto; padding: 8px 16px 64px; }}
.head {{ display: flex; align-items: center; gap: 10px; margin: 14px 0 4px; }}
h1 {{ font-size: 24px; margin: 0; flex: 1; }}
.btn {{ font: inherit; font-size: 13px; padding: 6px 12px; border-radius: 999px; border: 1px solid var(--line); background: transparent; color: inherit; text-decoration: none; cursor: pointer; }}
.day {{ font-size: 13px; text-transform: uppercase; letter-spacing: .06em; opacity: .6; margin: 22px 0 4px; }}
.row {{ display: flex; gap: 8px; align-items: flex-start; border-bottom: 1px solid var(--line); }}
.main {{ flex: 1; display: flex; gap: 14px; padding: 12px 0; color: inherit; text-decoration: none; }}
.txt {{ flex: 1; min-width: 0; }}
.t {{ font-weight: 600; font-size: 16px; line-height: 1.3; }}
.read .t {{ font-weight: 400; opacity: .6; }}
.m {{ font-size: 13px; opacity: .6; margin-top: 3px; }}
.s {{ font-size: 14px; opacity: .75; margin-top: 4px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }}
.th {{ width: 96px; height: 66px; object-fit: cover; border-radius: 8px; flex: none; background: #8882; }}
.save {{ padding: 12px 2px; font-size: 18px; color: inherit; opacity: .35; text-decoration: none; }}
.save.on {{ opacity: 1; color: var(--accent); }}
form.add {{ display: flex; gap: 8px; margin: 10px 0; }}
input {{ font: inherit; padding: 7px 12px; border-radius: 10px; border: 1px solid var(--line); background: transparent; color: inherit; }}
form.add input {{ flex: 1; }}
.msg {{ color: var(--accent); margin: 6px 0; }} .empty {{ opacity: .7; margin-top: 24px; }}
details {{ margin: 8px 0 4px; font-size: 14px; }} details li {{ margin: 6px 0; }} details li a {{ color: inherit; }}
details form {{ display: inline-flex; gap: 4px; margin-left: 8px; }} details form input {{ width: 110px; padding: 2px 8px; font-size: 13px; }}
details button {{ font-size: 12px; }} ul {{ padding-left: 18px; }}
.x {{ color: #c55 !important; margin-left: 8px; font-size: 13px; }} .tag {{ font-size: 11px; border: 1px solid #8886; border-radius: 6px; padding: 0 5px; }}
.err {{ color: #c90; font-size: 12px; }}
</style></head><body>
<div class="top"><nav class="bar">{bar}</nav></div>
<main>
<div class="head"><h1>{e(title or "Feeds")}</h1>
<a class="btn" href="airlock://feeds/readall?scope={q(scope)}">Mark all read</a>
<a class="btn" href="airlock://feeds/refresh?scope={q(scope)}">Refresh</a></div>
{f'<div class="msg">{e(message)}</div>' if message else ""}
<details><summary>Sources ({len(feedstore.feeds)}) · add, folders, OPML</summary>
<form class="add" action="airlock://feeds/add" method="get"><input name="url" placeholder="Site or feed address" autocomplete="off"><button class="btn">Follow</button></form>
<datalist id="folders">{folder_opts}</datalist>
<ul>{sources}</ul>
<a class="btn" href="airlock://feeds/export">Export OPML</a> <a class="btn" href="airlock://feeds/import">Import OPML…</a>
</details>
{"".join(rows)}
</main></body></html>"""
