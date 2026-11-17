# Airlock for Linux

The Airlock privacy browser on Linux: GTK 4, libadwaita and WebKitGTK (the same WebKit engine as the
iPhone and Mac versions, so the same blocking rules).

What's in this first version:

- Tabs and private tabs (each private tab has its own throwaway session)
- Search with DuckDuckGo, Startpage, Mojeek, Swisscows, Brave, Kagi, Qwant, Ecosia, Wolfram|Alpha (private),
  or Google / Perplexity (marked *)
- Ad blocking with EasyList, extra tracker blocking with EasyPrivacy, Airlock's own tracker list, cookie banners hidden
- HTTPS only (asks before plain HTTP), certificate errors refused, tracking parameters stripped from links
- Websites can't send you into your local network; third-party cookies off; Global Privacy Control
- Burn (Ctrl+Shift+Delete): closes every tab and erases browsing data
- Feeds: follow a site's RSS/Atom/JSON feed, or a page with no feed (Airlock reads its headlines)

## Install

**Download:** https://github.com/vspayce/airlock-linux/releases/latest/download/airlock.flatpak

Double-click the file (the COSMIC Store or GNOME Software opens it), or in a terminal:
`flatpak install --user airlock.flatpak`. The first install also fetches the GNOME runtime from Flathub.
Every passing build replaces this download, so the same link always gets the newest one.

Run from source (for development): `sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-webkit-6.0`, then
`python3 -m airlock`. Checks: `python3 -m airlock --selftest`.

The filter lists in `airlock/data` come from the Apple project's `tools/filters/convert.py` (copied in `tools/`).

## License

GNU General Public License v3.0 or later; see `LICENSE`. The bundled EasyList and EasyPrivacy rules are by
The EasyList authors; see `THIRD-PARTY-NOTICES.md`. Security problems: see `SECURITY.md`.
