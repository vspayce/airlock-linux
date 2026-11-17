"""Checks that need no window: address handling, local-network guard, feeds and page watching."""
import json
import os
import tempfile

from . import clip, feeds, netguard, urls


def _expect(name, ok, detail=""):
    return (name, bool(ok), detail)


def run_all():
    out = []
    # Addresses
    out.append(_expect("Typed names become HTTPS", urls.url_from_input("example.com", "duckduckgo") == "https://example.com"))
    s = urls.url_from_input("cats & dogs?x=1#y", "duckduckgo")
    out.append(_expect("Search text can't add URL parameters", s == "https://duckduckgo.com/?q=cats%20%26%20dogs%3Fx%3D1%23y", s))
    out.append(_expect("javascript: isn't opened as an address",
                       urls.url_from_input("javascript:alert(1)", "duckduckgo").startswith("https://duckduckgo.com/")))
    c = urls.clean("https://example.com/a?utm_source=x&id=7&fbclid=abc#top")
    out.append(_expect("Tracking parameters are removed", c == "https://example.com/a?id=7#top", c))
    c = urls.clean("https://www.google.com/url?q=https://example.com/page%3Futm_medium%3Dx&sa=D")
    out.append(_expect("Redirect links unwrap to the real page", c == "https://example.com/page", c))
    out.append(_expect("Redirects only unwrap to web pages",
                       urls.clean("https://www.google.com/url?q=javascript:alert(1)").startswith("https://www.google.com/")))
    # Local network
    local = ["localhost", "127.0.0.1", "10.0.0.1", "192.168.1.1", "172.16.0.1", "169.254.169.254", "100.64.0.1",
             "[::1]", "[::ffff:127.0.0.1]", "printer.local", "2130706433", "0x7f.1", "127.1", "router"]
    bad = [h for h in local if not netguard.is_local_host(h)]
    out.append(_expect("Local and private addresses are recognized", not bad, ", ".join(bad)))
    public = ["example.com", "8.8.8.8", "1.1.1.1", "localhost.example.com", "evil-10.0.0.1.example.com"]
    bad = [h for h in public if netguard.is_local_host(h)]
    out.append(_expect("Public names stay public", not bad, ", ".join(bad)))
    # Feeds
    rss = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Test</title>
      <item><title>One &amp; two</title><link>https://example.com/1</link><pubDate>Mon, 16 Nov 2026 10:00:00 GMT</pubDate>
      <description>&lt;script&gt;x()&lt;/script&gt;Hello &lt;b&gt;there&lt;/b&gt;</description></item>
      <item><title>Bad</title><link>javascript:alert(1)</link></item></channel></rss>"""
    parsed = feeds.parse_feed(rss, "https://example.com/feed")
    ok = parsed and parsed[0] == "Test" and len(parsed[1]) == 1 and parsed[1][0]["summary"] == "Hello there" \
        and parsed[1][0]["title"] == "One & two"
    out.append(_expect("RSS is read, scripts stripped, non-web links dropped", ok, repr(parsed)))
    atom = b"""<feed xmlns="http://www.w3.org/2005/Atom"><title>A</title><entry><title>E</title>
      <link rel="alternate" href="/e1"/><id>x1</id><updated>2026-11-16T10:00:00Z</updated></entry></feed>"""
    parsed = feeds.parse_feed(atom, "https://example.com/atom")
    out.append(_expect("Atom is read", parsed and parsed[1][0]["link"] == "https://example.com/e1", repr(parsed)))
    page = b"""<html><head><title>Example News</title><meta property="og:site_name" content="Example Daily"></head><body>
      <header><a href="/about">About us and our team</a></header>
      <nav><a href="/world/news-from-everywhere">World news from everywhere</a></nav>
      <h2><a href="/2026/11/05/city-council-approves-new-park">City council approves new park</a></h2>
      <article><a href="https://www.example.com/story/rain-expected-this-weekend"><h3>Rain expected this weekend</h3></a></article>
      <h2><a href="https://tracker.ads.example.net/click?id=1">Sponsored offer you will love</a></h2>
      <h3><a href="/tag/politics">Politics coverage</a></h3>
      <h2><a href="/local/school-board-meets-tuesday#comments">School board meets Tuesday</a></h2>
      <script>document.write('<h2><a href="/fake-script-headline">Fake script headline</a></h2>')</script></body></html>"""
    title, items = feeds.parse_page(page, "https://www.example.com/")
    titles = [i["title"] for i in items]
    out.append(_expect("Pages without a feed: headlines are read, navigation isn't",
                       title == "Example Daily" and titles == ["City council approves new park", "School board meets Tuesday",
                                                               "Rain expected this weekend"] and items[0]["date"],
                       f"{title} {titles}"))
    html_out = feeds.render(type("S", (), {"feeds": [{"id": "f", "title": "<b>x</b>", "url": "https://e.com"}],
                                           "items": [{"id": "i", "feed": "f", "title": "<script>alert(1)</script>",
                                                      "link": "https://e.com/\"onmouseover=x", "date": 0, "summary": ""}],
                                           "read": set()})())
    out.append(_expect("Feeds page escapes everything from feeds",
                       "<script>alert" not in html_out and "<b>x</b>" not in html_out and '"onmouseover' not in html_out))
    # Clipping
    art = {"title": 'He said "hi"\n---\nevil: true', "url": "https://example.com/a/b",
           "content": '<p>Hello <b>bold</b> <a href="/x">link</a> <a href="javascript:alert(1)">bad</a></p>'
                      '<script>alert(1)</script><blockquote><p>Q</p></blockquote><img src="/i.png" onerror="x()">'}
    n = clip.note(art, ["news", "a b!"])
    front = n.split("---\n")[1]
    out.append(_expect("Clip front matter can't be broken out of",
                       front.count("\n") == 4 and json.loads(front.splitlines()[0][7:]) == art["title"], front))
    md = n.split("---\n", 2)[2]
    out.append(_expect("Clips become clean Markdown",
                       "**bold**" in md and "[link](https://example.com/x)" in md and "javascript" not in md
                       and "alert" not in md and "> Q" in md and "![](https://example.com/i.png)" in md, md))
    page = clip.reader_page(art)
    out.append(_expect("Reader page has no scripts or handlers",
                       "<script" not in page and "onerror" not in page and "javascript:" not in page))
    with tempfile.TemporaryDirectory() as d:
        p1 = clip.save_to_folder(d, art, subfolder="Clippings")
        p2 = clip.save_to_folder(d, art, subfolder="Clippings")
        bad = None
        try:
            clip.save_to_folder(d, art, subfolder="../../etc")
        except ValueError:
            bad = "refused"
        out.append(_expect("Clips stay in their folder and never overwrite",
                           p1 != p2 and os.path.dirname(p1) == os.path.join(d, "Clippings") and bad == "refused",
                           f"{p1} {p2} {bad}"))
    return out


if __name__ == "__main__":
    for n, ok, d in run_all():
        print("PASS " if ok else "FAIL ", n, d)
