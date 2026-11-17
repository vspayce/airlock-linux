#!/usr/bin/env python3
"""Turns EasyList and EasyPrivacy (Adblock Plus syntax) into Safari/WebKit content-blocker rules.

Usage:  python3 convert.py <easylist-repo-dir> <output-dir>

Writes, for each list, one or more JSON files (WebKit allows 150,000 rules per compiled list, so big
lists are split) plus filters-info.json describing what was built. Rules WebKit can't express
(scriptlets, redirects, regex filters, extended CSS) are skipped and counted.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone

import soupsieve

MAX_RULES = 140_000
SELECTORS_PER_RULE = 250

TYPE_MAP = {
    "script": ["script"],
    "image": ["image"],
    "stylesheet": ["style-sheet"],
    "font": ["font"],
    "media": ["media"],
    "xmlhttprequest": ["fetch"],
    "ping": ["ping"],
    "websocket": ["websocket"],
    "other": ["other"],
    "popup": ["popup"],
    "subdocument": ["document"],   # with load-context child-frame
    "document": ["document"],
    "object": ["other"],
}
ALL_TYPES = ["script", "image", "style-sheet", "font", "media", "fetch", "ping", "websocket", "other"]
SKIP_OPTIONS = {"redirect", "redirect-rule", "rewrite", "csp", "removeparam", "method", "header",
                "permissions", "replace", "badfilter", "denyallow", "to", "urltransform", "uritransform",
                "removeheader", "empty", "mp4", "cookie", "jsonprune", "sitekey"}
EXTENDED_CSS = (":-abp", ":has-text", ":contains", ":xpath", ":matches-", ":upward", ":remove",
                ":style", ":watch-attr", ":min-text-length", ":others", ":if", ":nth-ancestor",
                ":matches-path", ":-ext-")


def expand(repo, template):
    out = []
    for line in open(os.path.join(repo, template), encoding="utf-8"):
        m = re.match(r"%include easylist:(.+)%", line.strip())
        if m:
            path = os.path.join(repo, m.group(1))
            if os.path.exists(path):
                out.extend(open(path, encoding="utf-8").read().splitlines())
        else:
            out.append(line.rstrip("\n"))
    return out


def ascii_domain(d):
    d = d.strip().lower()
    if not d or "*" in d or "/" in d:
        return None
    try:
        return d.encode("idna").decode("ascii")
    except Exception:
        return None


def pattern_to_regex(p):
    """ABP URL pattern -> WebKit url-filter regex, or None if WebKit can't express it."""
    if p.startswith("/") and p.endswith("/") and len(p) > 2:
        return None  # real regex filters: skipped
    if any(ord(c) > 127 for c in p):
        return None
    out = ""
    i = 0
    if p.startswith("||"):
        out = r"^[^:]+://+([^:/]+\.)?"
        i = 2
    elif p.startswith("|"):
        out = "^"
        i = 1
    end_anchor = p.endswith("|") and not p.endswith("||")
    body = p[i:-1] if end_anchor else p[i:]
    # A trailing ^ also matches the end of the address, which WebKit can't say (no alternation).
    # After a path it's dropped (slightly broader); after a bare host a / : ? always follows anyway.
    if body.endswith("^") and "/" in body.rstrip("^"):
        body = body[:-1]
    for c in body:
        if c == "*":
            out += ".*"
        elif c == "^":
            out += "[/:?&=]"
        elif c in ".+?()[]{}\\$|":
            out += "\\" + c
        else:
            out += c
    if end_anchor:
        out += "$"
    if not out or out == "^":
        out = ".*"
    if "{" in out.replace("\\{", "") or "|" in out.replace("\\|", ""):
        return None
    return out


def parse_network(line):
    exception = line.startswith("@@")
    if exception:
        line = line[2:]
    opts = {}
    if "$" in line and not line.startswith("/"):
        pat, _, optstr = line.rpartition("$")
        if not pat:
            pat = line
            optstr = ""
        for o in optstr.split(","):
            o = o.strip()
            if not o:
                continue
            k, _, v = o.partition("=")
            opts[k.lower()] = v
    elif "$" in line and line.startswith("/"):
        return None
    else:
        pat = line
    if any(k.lstrip("~") in SKIP_OPTIONS for k in opts):
        return None
    if "important" in opts:
        opts.pop("important")
    regex = pattern_to_regex(pat)
    if regex is None:
        return None
    trigger = {"url-filter": regex}
    if "match-case" in opts:
        trigger["url-filter-is-case-sensitive"] = True
        opts.pop("match-case")
    # domains
    if "domain" in opts:
        pos, neg = [], []
        for d in opts.pop("domain").split("|"):
            negate = d.startswith("~")
            ad = ascii_domain(d.lstrip("~"))
            if not ad:
                return None
            (neg if negate else pos).append("*" + ad)
        if pos and neg:
            return None
        if pos:
            trigger["if-domain"] = pos
        elif neg:
            trigger["unless-domain"] = neg
    # party
    if "third-party" in opts or "3p" in opts:
        trigger["load-type"] = ["third-party"]
    elif "~third-party" in opts or "1p" in opts or "first-party" in opts:
        trigger["load-type"] = ["first-party"]
    for k in ("third-party", "3p", "~third-party", "1p", "first-party"):
        opts.pop(k, None)
    # cosmetic switches on exceptions
    cosmetic_switch = None
    for k in ("generichide", "elemhide", "ghide", "ehide"):
        if k in opts:
            cosmetic_switch = "generic" if k in ("generichide", "ghide") else "all"
            opts.pop(k)
    if cosmetic_switch:
        return {"kind": "cosmetic-exception", "scope": cosmetic_switch, "pattern": pat, "if": trigger.get("if-domain")}
    # resource types
    pos_types, neg_types, child_frame, whole_site = [], [], False, False
    for k in list(opts):
        base = k.lstrip("~")
        if base in TYPE_MAP:
            if k.startswith("~"):
                neg_types += TYPE_MAP[base]
            else:
                pos_types += TYPE_MAP[base]
                if base == "subdocument":
                    child_frame = True
                if base == "document":
                    whole_site = True
            opts.pop(k)
        elif base in ("all",):
            opts.pop(k)
    if opts:  # anything left we don't understand
        return None
    if pos_types:
        trigger["resource-type"] = sorted(set(pos_types))
    elif neg_types:
        trigger["resource-type"] = sorted(set(ALL_TYPES) - set(neg_types))
    if child_frame and not whole_site and pos_types == ["document"]:
        trigger["load-context"] = ["child-frame"]
    if exception and whole_site:
        # @@||site^$document : the whole site is allowed.
        dom = re.match(r"^\|\|([a-z0-9.-]+)\^?$", pat)
        if not dom:
            return None
        trigger = {"url-filter": ".*", "if-domain": ["*" + dom.group(1)]}
    action = {"type": "ignore-previous-rules" if exception else "block"}
    return {"kind": "exception" if exception else "block", "rule": {"trigger": trigger, "action": action}}


def selector_ok(sel):
    if not sel or any(x in sel for x in EXTENDED_CSS) or any(ord(c) > 127 for c in sel):
        return False
    try:
        soupsieve.compile(sel)
        return True
    except Exception:
        return False


def convert(lines, name):
    blocks, exceptions = [], []
    generic, generic_unless = [], {}   # selector -> set(unless domains)
    specific = {}                       # tuple(if domains) -> [selectors]
    generic_off = set()                 # sites where generic hiding is off
    all_off = set()
    generic_exceptions = set()
    skipped = 0
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("!") or line.startswith("["):
            continue
        if "#@#" in line or "##" in line:
            exc = "#@#" in line
            doms, _, sel = line.partition("#@#" if exc else "##")
            if not selector_ok(sel):
                skipped += 1
                continue
            pos, neg = [], []
            for d in [d for d in doms.split(",") if d]:
                ad = ascii_domain(d.lstrip("~"))
                if not ad:
                    continue
                (neg if d.startswith("~") else pos).append("*" + ad)
            if exc:
                if not pos:
                    generic_exceptions.add(sel)
                else:
                    generic_unless.setdefault(sel, set()).update(pos)
                continue
            if not doms:
                generic.append(sel)
            elif pos:
                specific.setdefault(tuple(sorted(pos)), []).append(sel)
            elif neg:
                generic_unless.setdefault(sel, set()).update(neg)
            continue
        if "#?#" in line or "#$#" in line or "#%#" in line or "$$" in line:
            skipped += 1
            continue
        r = parse_network(line)
        if r is None:
            skipped += 1
            continue
        if r["kind"] == "cosmetic-exception":
            m = re.match(r"^\|\|([a-z0-9.-]+)\^?$", r["pattern"])
            if m:
                (generic_off if r["scope"] == "generic" else all_off).add("*" + m.group(1))
            elif r.get("if"):
                (generic_off if r["scope"] == "generic" else all_off).update(r["if"])
            continue
        (exceptions if r["kind"] == "exception" else blocks).append(r["rule"])

    css = []
    plain = [s for s in dict.fromkeys(generic) if s not in generic_exceptions and s not in generic_unless]
    off = sorted(generic_off | all_off)
    for i in range(0, len(plain), SELECTORS_PER_RULE):
        trig = {"url-filter": ".*"}
        if off:
            trig["unless-domain"] = off
        css.append({"trigger": trig, "action": {"type": "css-display-none", "selector": ", ".join(plain[i:i + SELECTORS_PER_RULE])}})
    for sel, unless in generic_unless.items():
        if sel in generic_exceptions:
            continue
        css.append({"trigger": {"url-filter": ".*", "unless-domain": sorted(set(unless) | set(off))},
                    "action": {"type": "css-display-none", "selector": sel}})
    for doms, sels in specific.items():
        doms = [d for d in doms if d not in all_off]
        if not doms:
            continue
        sels = list(dict.fromkeys(sels))
        for i in range(0, len(sels), SELECTORS_PER_RULE):
            css.append({"trigger": {"url-filter": ".*", "if-domain": list(doms)},
                        "action": {"type": "css-display-none", "selector": ", ".join(sels[i:i + SELECTORS_PER_RULE])}})

    # Exceptions go last in every part, so they cancel the blocks before them.
    body = blocks + css
    room = MAX_RULES - len(exceptions)
    parts = [body[i:i + room] + exceptions for i in range(0, max(len(body), 1), room)]
    return parts, {"blocks": len(blocks), "cosmeticRules": len(css), "exceptions": len(exceptions), "skipped": skipped}


def main():
    repo, outdir = sys.argv[1], sys.argv[2]
    os.makedirs(outdir, exist_ok=True)
    info = {"built": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "lists": {}}
    for name, template in (("easylist", "easylist.template"), ("easyprivacy", "easyprivacy.template")):
        parts, stats = convert(expand(repo, template), name)
        files = []
        for n, part in enumerate(parts, 1):
            fn = f"{name}-{n}.json"
            with open(os.path.join(outdir, fn), "w", encoding="utf-8") as f:
                json.dump(part, f, separators=(",", ":"), ensure_ascii=True)
            files.append(fn)
        stats["files"] = files
        info["lists"][name] = stats
        print(name, stats)
    with open(os.path.join(outdir, "filters-info.json"), "w") as f:
        json.dump(info, f, indent=1)


if __name__ == "__main__":
    main()
