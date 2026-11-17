"""Reader view and clipping: turn the article Readability found into a clean page or a Markdown note."""
import html
import json
import os
import re
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

DATA = os.path.join(os.path.dirname(__file__), "data")


def extract_script():
    """Runs in Airlock's private script world: the page can't see or change it."""
    with open(os.path.join(DATA, "Readability.js"), encoding="utf-8") as f:
        lib = f.read()
    return lib + """
;(function () {
  try {
    var doc = document.cloneNode(true);
    var a = new Readability(doc, { charThreshold: 300 }).parse();
    if (!a) return JSON.stringify({ ok: false });
    return JSON.stringify({ ok: true, title: a.title || document.title, byline: a.byline || '', content: a.content || '',
      excerpt: a.excerpt || '', site: a.siteName || '', published: a.publishedTime || '', lang: a.lang || '',
      url: location.href });
  } catch (e) { return JSON.stringify({ ok: false, error: String(e) }); }
})();
"""


# MARK: Sanitizing (the reader page only ever shows this subset of HTML)

ALLOWED = {"p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "blockquote", "pre", "code",
           "em", "i", "strong", "b", "a", "img", "figure", "figcaption", "table", "thead", "tbody", "tr", "th",
           "td", "sup", "sub", "dl", "dt", "dd", "del", "s", "mark", "small", "picture", "div", "span", "section"}
DROP_WITH_CONTENT = {"script", "style", "noscript", "iframe", "object", "embed", "form", "button", "input",
                     "select", "textarea", "svg", "math", "template", "video", "audio", "canvas", "source"}
VOID = {"br", "hr", "img"}


def _safe_url(value, base):
    if not value:
        return None
    url = urljoin(base, value.strip())
    p = urlsplit(url)
    return url if p.scheme in ("http", "https") and p.hostname else None


class _Sanitizer(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.out, self.skip = base, [], 0

    def handle_starttag(self, tag, attrs):
        if tag in DROP_WITH_CONTENT:
            if tag not in VOID:
                self.skip += 1
            return
        if self.skip or tag not in ALLOWED:
            return
        a = dict(attrs)
        keep = ""
        if tag == "a":
            href = _safe_url(a.get("href"), self.base)
            keep = f' href="{html.escape(href)}"' if href else ""
        elif tag == "img":
            src = _safe_url(a.get("src") or a.get("data-src"), self.base)
            if not src:
                return
            keep = f' src="{html.escape(src)}" alt="{html.escape(a.get("alt") or "")}" loading="lazy"'
        elif tag in ("td", "th"):
            for k in ("colspan", "rowspan"):
                if (a.get(k) or "").isdigit():
                    keep += f' {k}="{a[k]}"'
        self.out.append(f"<{tag}{keep}>")

    def handle_endtag(self, tag):
        if tag in DROP_WITH_CONTENT:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip or tag not in ALLOWED or tag in VOID:
            return
        self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(html.escape(data))


def sanitize(content, base):
    s = _Sanitizer(base)
    s.feed(content or "")
    s.close()
    return "".join(s.out)


def reader_page(article):
    e = html.escape
    body = sanitize(article.get("content", ""), article.get("url", ""))
    meta = " · ".join(x for x in (article.get("site"), article.get("byline")) if x)
    host = urlsplit(article.get("url", "")).hostname or ""
    return f"""<!doctype html><html lang="{e(article.get('lang') or 'en')}"><head><meta charset="utf-8">
<title>{e(article.get('title') or 'Reader')}</title>
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src https: data:; style-src 'unsafe-inline'; form-action 'none'">
<style>
:root {{ color-scheme: light dark; }}
@media (prefers-color-scheme: dark) {{ body {{ background: #15191f; color: #dfe3e8; }} a {{ color: #7fd6d6; }} }}
body {{ font: 19px/1.65 Georgia, "Noto Serif", serif; max-width: 680px; margin: 0 auto; padding: 40px 22px 80px; }}
h1 {{ font: 700 32px/1.2 system-ui, sans-serif; margin: 0 0 8px; }}
.meta {{ font: 14px system-ui, sans-serif; opacity: .65; margin-bottom: 28px; }}
img {{ max-width: 100%; height: auto; border-radius: 8px; }} figure {{ margin: 1.4em 0; }}
figcaption {{ font: 14px system-ui, sans-serif; opacity: .7; }}
pre {{ overflow: auto; font-size: 15px; background: #8881; padding: 12px; border-radius: 8px; }}
blockquote {{ border-left: 3px solid #8886; margin-left: 0; padding-left: 16px; opacity: .9; }}
table {{ border-collapse: collapse; }} td, th {{ border: 1px solid #8884; padding: 4px 8px; }}
</style></head><body>
<h1>{e(article.get('title') or '')}</h1>
<div class="meta">{e(meta or host)} · <a href="{e(article.get('url', ''))}">Original page</a></div>
{body}
</body></html>"""


# MARK: Markdown

class _Markdown(HTMLParser):
    BLOCK = {"p", "div", "section", "figure", "figcaption", "table", "dl"}

    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.out, self.skip = base, [], 0
        self.lists, self.link, self.pre, self.quotes = [], [], False, []

    def _nl(self, n=2):
        text = "".join(self.out)
        stripped = text.rstrip(" ")
        have = len(stripped) - len(stripped.rstrip("\n"))
        if stripped and have < n:
            self.out.append("\n" * (n - have))

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in DROP_WITH_CONTENT:
            self.skip += 1
            return
        if self.skip:
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._nl()
            self.out.append("#" * int(tag[1]) + " ")
        elif tag in self.BLOCK:
            self._nl()
        elif tag == "br":
            self.out.append("  \n")
        elif tag == "hr":
            self._nl()
            self.out.append("---")
            self._nl()
        elif tag in ("strong", "b"):
            self.out.append("**")
        elif tag in ("em", "i"):
            self.out.append("*")
        elif tag == "code" and not self.pre:
            self.out.append("`")
        elif tag == "pre":
            self._nl()
            self.out.append("```\n")
            self.pre = True
        elif tag == "blockquote":
            self._nl()
            self.quotes.append(len(self.out))
        elif tag in ("ul", "ol"):
            self.lists.append([tag, 0])
            self._nl(1 if len(self.lists) > 1 else 2)
        elif tag == "li":
            self._nl(1)
            kind = self.lists[-1] if self.lists else ["ul", 0]
            kind[1] += 1
            self.out.append("  " * (len(self.lists) - 1) + ("- " if kind[0] == "ul" else f"{kind[1]}. "))
        elif tag == "a":
            self.link.append((_safe_url(a.get("href"), self.base), len(self.out)))
            self.out.append("[")
        elif tag == "img":
            src = _safe_url(a.get("src") or a.get("data-src"), self.base)
            if src:
                alt = (a.get("alt") or "").replace("]", "")
                self.out.append(f"![{alt}]({src})")

    def handle_endtag(self, tag):
        if tag in DROP_WITH_CONTENT:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6") or tag in self.BLOCK:
            self._nl()
        elif tag in ("strong", "b"):
            self.out.append("**")
        elif tag in ("em", "i"):
            self.out.append("*")
        elif tag == "code" and not self.pre:
            self.out.append("`")
        elif tag == "pre":
            self.out.append("\n```")
            self.pre = False
            self._nl()
        elif tag == "blockquote" and self.quotes:
            start = self.quotes.pop()
            inner = "".join(self.out[start:]).strip("\n")
            del self.out[start:]
            self.out.append("\n".join(("> " + line).rstrip() for line in inner.split("\n")))
            self._nl()
        elif tag in ("ul", "ol"):
            if self.lists:
                self.lists.pop()
            self._nl()
        elif tag == "a":
            href, start = self.link.pop() if self.link else (None, None)
            if href:
                self.out.append(f"]({href})")
            elif start is not None and start < len(self.out):
                self.out[start] = ""          # not a web link: keep just the text

    def handle_data(self, data):
        if self.skip:
            return
        if self.pre:
            self.out.append(data)
            return
        text = re.sub(r"\s+", " ", data)
        if not "".join(self.out[-1:]).strip() and (not self.out or self.out[-1].endswith(("\n", "> ", "- ", ". "))):
            text = text.lstrip()
        # Characters that would start Markdown formatting by accident.
        text = re.sub(r"([\\`*_\[\]<>])", r"\\\1", text)
        self.out.append(text)


def to_markdown(content, base):
    m = _Markdown(base)
    m.feed(content or "")
    m.close()
    text = "".join(m.out)
    text = re.sub(r"(?<!!)\[\]\([^)]*\)", "", text)          # empty links (not images)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def _yaml(value):
    # JSON strings are valid YAML, so nothing in a title can break out of the front matter.
    return json.dumps(value, ensure_ascii=False)


def note(article, tags=(), title=None):
    title = (title or article.get("title") or "Untitled").strip()
    lines = ["---", f"title: {_yaml(title)}", f"source: {_yaml(article.get('url', ''))}"]
    if article.get("byline"):
        lines.append(f"author: {_yaml(article['byline'])}")
    if article.get("published"):
        lines.append(f"published: {_yaml(article['published'])}")
    lines.append(f"clipped: {_yaml(datetime.now().astimezone().isoformat(timespec='seconds'))}")
    clean_tags = [t for t in (re.sub(r"[^\w/-]", "", t.strip().lstrip("#")) for t in tags) if t]
    lines.append("tags: [" + ", ".join(_yaml(t) for t in ["clippings", *clean_tags]) + "]")
    lines.append("---")
    return "\n".join(lines) + "\n\n" + to_markdown(article.get("content", ""), article.get("url", ""))


def file_name(title):
    name = re.sub(r'[\x00-\x1f/\\:*?"<>|#^\[\]]', " ", title or "Untitled")
    name = re.sub(r"\s+", " ", name).strip().strip(".")[:120].strip() or "Untitled"
    return name + ".md"


def save_to_folder(folder, article, tags=(), title=None, subfolder="Clippings"):
    """Writes the note into folder/subfolder without ever leaving it or overwriting another note."""
    target = os.path.realpath(os.path.join(folder, subfolder)) if subfolder else os.path.realpath(folder)
    if not target.startswith(os.path.realpath(folder)):
        raise ValueError("Clip folder is outside the chosen folder")
    os.makedirs(target, exist_ok=True)
    base = file_name(title or article.get("title"))
    path = os.path.join(target, base)
    n = 2
    while os.path.exists(path):
        path = os.path.join(target, base[:-3] + f" {n}.md")
        n += 1
    with open(path, "x", encoding="utf-8") as f:
        f.write(note(article, tags, title))
    return path
