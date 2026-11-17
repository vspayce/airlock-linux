"""Site info lookups: domain registration (RDAP, the modern whois), DNS records (Cloudflare DNS over HTTPS)
and who hosts each address. Looked up only when asked, over HTTPS, with no cookies and nothing about you."""
import json
from datetime import datetime, timezone
from urllib.parse import quote

from . import feeds


def _json(url, accept="application/json"):
    data, _ = feeds.fetch(url, accept=accept)
    return json.loads(data[:1_000_000])


def _date(s):
    try:
        return datetime.fromisoformat((s or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def _vcard_name(entity):
    card = entity.get("vcardArray")
    if not isinstance(card, list) or len(card) < 2:
        return None
    for prop in card[1]:
        if isinstance(prop, list) and prop and prop[0] == "fn" and isinstance(prop[-1], str) and prop[-1]:
            return prop[-1][:120]
    return None


def whois(host):
    """Registration record for the host's registered domain (tries news.bbc.co.uk, then bbc.co.uk...)."""
    labels = host.lower().strip(".").split(".")
    while len(labels) >= 2:
        name = ".".join(labels)
        try:
            o = _json("https://rdap.org/domain/" + quote(name), "application/rdap+json")
        except Exception:  # noqa: BLE001 - not registered at this level; try the parent
            labels = labels[1:]
            continue
        w = {"domain": (o.get("ldhName") or name).lower(), "registrar": None, "registrant": None,
             "created": None, "expires": None, "updated": None,
             "status": [s.capitalize() for s in o.get("status", []) if isinstance(s, str)],
             "nameservers": [n.get("ldhName", "").lower() for n in o.get("nameservers", []) if isinstance(n, dict)]}
        for e in o.get("events", []):
            d = _date(e.get("eventDate"))
            key = {"registration": "created", "expiration": "expires", "last changed": "updated"}.get(e.get("eventAction"))
            if key and d:
                w[key] = d
        for ent in o.get("entities", []):
            roles = ent.get("roles", [])
            n = _vcard_name(ent)
            if "registrar" in roles:
                w["registrar"] = n
            if "registrant" in roles and n and "redacted" not in n.lower():
                w["registrant"] = n
        return w
    raise LookupError("No registration record found")


TYPES = [("A", 1), ("AAAA", 28), ("CNAME", 5), ("MX", 15), ("NS", 2), ("TXT", 16), ("CAA", 257)]


def dns(host):
    out, ok, last_error = [], False, None
    for name, code in TYPES:
        try:
            o = _json(f"https://cloudflare-dns.com/dns-query?name={quote(host)}&type={name}", "application/dns-json")
        except Exception as e:  # noqa: BLE001
            last_error = e
            continue
        ok = True
        for a in o.get("Answer", []):
            if a.get("type") == code and isinstance(a.get("data"), str):
                v = a["data"].strip('"').rstrip(".")[:400]
                if (name, v) not in out:
                    out.append((name, v))
    if not ok:
        raise LookupError(f"DNS lookup failed: {last_error}")
    return out


def ip_owner(ip):
    try:
        o = _json("https://rdap.org/ip/" + quote(ip), "application/rdap+json")
    except Exception:  # noqa: BLE001
        return None
    owner = None
    for ent in o.get("entities", []):
        roles = ent.get("roles", [])
        if "registrant" in roles or "administrative" in roles:
            owner = _vcard_name(ent)
            if owner:
                break
    cidr = None
    for c in o.get("cidr0_cidrs", []):
        prefix = c.get("v4prefix") or c.get("v6prefix")
        if prefix and c.get("length") is not None:
            cidr = f"{prefix}/{c['length']}"
            break
    return {"ip": ip, "owner": owner, "network": o.get("name"), "country": o.get("country"), "cidr": cidr}


def age(d):
    days = (datetime.now(timezone.utc) - d).days
    if days < 60:
        return f"{days} days old"
    if days < 730:
        return f"{days // 30} months old"
    return f"{days // 365} years old"
