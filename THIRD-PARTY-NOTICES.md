# Third-party notices

Airlock for Linux is licensed under the GNU General Public License, version 3 or later (see `LICENSE`).
It includes or uses the following work by others.

## Filter lists (bundled)

`airlock/data/easylist-1.json.z` and `airlock/data/easyprivacy-1.json.z` are converted, compressed copies of
**EasyList** and **EasyPrivacy** by The EasyList authors (https://easylist.to/), converted to WebKit
content-blocker rules by `tools/convert.py`. They are used under the GNU General Public License v3
(EasyList is dual-licensed GPLv3 / Creative Commons Attribution-ShareAlike 3.0 Unported or later).
Source: https://github.com/easylist/easylist

`airlock/data/airlock-lists.json` (Airlock's own tracker domains and cookie-banner selectors) is part of
Airlock and covered by its license.

## Libraries used at run time (not bundled; provided by the GNOME runtime or your system)

| Library | License |
|---|---|
| WebKitGTK | LGPL-2.1 / BSD-2-Clause |
| GTK 4 | LGPL-2.1-or-later |
| libadwaita | LGPL-2.1-or-later |
| GLib / GObject / GIO | LGPL-2.1-or-later |
| PyGObject | LGPL-2.1-or-later |
| Python | PSF License |

## Readability (bundled)

`airlock/data/Readability.js` is Mozilla's Readability library (https://github.com/mozilla/readability,
commit ab4027a8), Copyright (c) 2010 Arc90 Inc and Mozilla, licensed under the Apache License 2.0.
It finds the article on a page for Reader view and clipping. Apache-2.0 code may be combined with GPL-3.0 code.
