"""Address bar input, search engines and tracking-parameter cleaning (ported from the Apple version)."""
from urllib.parse import quote, urlsplit, urlunsplit, parse_qsl

SEARCH_ENGINES = {
    # id: (title, url template, private)
    "duckduckgo": ("DuckDuckGo", "https://duckduckgo.com/?q={}", True),
    "startpage": ("Startpage", "https://www.startpage.com/do/search?q={}", True),
    "mojeek": ("Mojeek", "https://www.mojeek.com/search?q={}", True),
    "swisscows": ("Swisscows", "https://swisscows.com/en/web?query={}", True),
    "brave": ("Brave Search", "https://search.brave.com/search?q={}", True),
    "kagi": ("Kagi", "https://kagi.com/search?q={}", True),
    "qwant": ("Qwant", "https://www.qwant.com/?q={}", True),
    "ecosia": ("Ecosia", "https://www.ecosia.org/search?q={}", True),
    "wolframalpha": ("Wolfram|Alpha", "https://www.wolframalpha.com/input?i={}", True),
    "google": ("Google", "https://www.google.com/search?q={}", False),
    "perplexity": ("Perplexity", "https://www.perplexity.ai/search?q={}", False),
}


def engine_label(engine_id):
    title, _, private = SEARCH_ENGINES[engine_id]
    return title if private else title + " *"


def search_url(text, engine_id):
    _, template, _ = SEARCH_ENGINES.get(engine_id, SEARCH_ENGINES["duckduckgo"])
    # quote() leaves nothing that could add parameters (& = ? # + are all encoded).
    return template.format(quote(text, safe=""))


def url_from_input(text, engine_id):
    """What the user typed -> an address to open (or a search)."""
    text = text.strip()
    if not text:
        return None
    parts = urlsplit(text)
    if parts.scheme.lower() in ("http", "https") and parts.hostname:
        return text
    if parts.scheme.lower() == "airlock":
        return text
    if " " not in text and "." in text and "://" not in text:
        candidate = urlsplit("https://" + text)
        host = candidate.hostname or ""
        if "." in host and not host.startswith(".") and not host.endswith("."):
            return "https://" + text
    if text.startswith("localhost"):
        return "http://" + text
    return search_url(text, engine_id)


TRACKING_PARAMS = {
    "fbclid", "gclid", "dclid", "gbraid", "wbraid", "msclkid", "mc_eid", "mc_cid", "igshid", "igsh",
    "yclid", "_hsenc", "_hsmi", "mkt_tok", "oly_anon_id", "oly_enc_id", "vero_id", "vero_conv",
    "ttclid", "twclid", "li_fat_id", "srsltid", "_gl", "ref_src", "ref_url", "s_cid", "spm",
}


def _param(query, name):
    for k, v in parse_qsl(query, keep_blank_values=True):
        if k == name:
            p = urlsplit(v)
            if p.scheme.lower() in ("http", "https") and p.hostname:
                return v
    return None


def _unwrap_redirect(parts):
    host = (parts.hostname or "").lower()
    if (host.startswith("www.google.") or host.startswith("google.")) and parts.path == "/url":
        return _param(parts.query, "q") or _param(parts.query, "url")
    if host in ("l.facebook.com", "lm.facebook.com") and parts.path == "/l.php":
        return _param(parts.query, "u")
    if host == "l.instagram.com":
        return _param(parts.query, "u")
    if host == "out.reddit.com":
        return _param(parts.query, "url")
    return None


def clean(url, depth=0):
    """Removes tracking parameters and unwraps known redirect links. Returns the input when nothing changes."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    inner = _unwrap_redirect(parts)
    if inner and depth < 4:
        return clean(inner, depth + 1)
    if not parts.query:
        return url
    pieces = parts.query.split("&")
    kept = []
    for piece in pieces:
        name = piece.split("=", 1)[0].lower()
        if name.startswith("utm_") or name in TRACKING_PARAMS:
            continue
        kept.append(piece)
    if len(kept) == len(pieces):
        return url
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "&".join(kept), parts.fragment))


def upgrade_to_https(url):
    parts = urlsplit(url)
    if parts.scheme.lower() != "http":
        return url
    return urlunsplit(("https",) + tuple(parts[1:]))


def host_of(url):
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def site_key(host):
    host = host.lower().strip(".")
    return host[4:] if host.startswith("www.") else host
