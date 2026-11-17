"""Keeps websites (and Airlock's own fetches) out of your local network."""
import ipaddress
import socket


def _parse_ip(host):
    host = host.strip("[]").rstrip(".")
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    # Numeric spellings browsers accept: 2130706433, 0x7f.1, 127.1
    try:
        return ipaddress.ip_address(socket.inet_ntoa(socket.inet_aton(host)))
    except (OSError, ValueError):
        return None


def _is_private_ip(ip):
    if ip.version == 6:
        mapped = ip.ipv4_mapped or ip.sixtofour
        if mapped:
            return _is_private_ip(mapped)
        if ip.teredo:
            return True
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved
            or ip.is_unspecified or ip in ipaddress.ip_network("100.64.0.0/10"))


def is_local_host(host):
    """True for localhost, .local / .internal names, and private, loopback or carrier-NAT addresses."""
    if not host:
        return True
    h = host.lower().strip("[]").rstrip(".")
    if h == "localhost" or h.endswith(".localhost") or h.endswith(".local") or h.endswith(".internal") \
            or h.endswith(".lan") or h.endswith(".home.arpa") or "." not in h and ":" not in h and _parse_ip(h) is None:
        return True
    ip = _parse_ip(h)
    return ip is not None and _is_private_ip(ip)


def resolves_public(host):
    """For Airlock's own fetches: every address the name resolves to must be public."""
    if is_local_host(host):
        return False
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if _is_private_ip(ip):
            return False
    return bool(infos)
