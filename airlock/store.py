"""Settings and small JSON files in Airlock's own data folder."""
import json
import os
import tempfile

from gi.repository import GLib

DATA_DIR = os.path.join(GLib.get_user_data_dir(), "airlock")
CACHE_DIR = os.path.join(GLib.get_user_cache_dir(), "airlock")
os.makedirs(DATA_DIR, mode=0o700, exist_ok=True)
os.makedirs(CACHE_DIR, mode=0o700, exist_ok=True)


def read_json(name, default):
    try:
        with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(name, value):
    fd, tmp = tempfile.mkstemp(dir=DATA_DIR, prefix=".tmp-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(value, f, indent=1)
    os.replace(tmp, os.path.join(DATA_DIR, name))


DEFAULTS = {
    "engine": "duckduckgo",
    "protection": "standard",      # standard: tracker cookies stripped; strict: trackers blocked outright
    "block_ads": True,             # EasyList
    "easyprivacy": True,           # EasyPrivacy
    "hide_cookie_banners": True,
    "strip_tracking": True,
    "https_only": True,
    "gpc": True,
    "clear_on_quit": False,
    "bar_at_bottom": True,
    "shields_off": [],             # sites where Airlock's blocking is off
    "clip_destination": "folder",
    "clip_folder": "",
    "clip_subfolder": "Clippings",
    "clip_tags": "",
    "generic_ua": False,
    "favorites": [],               # [{"title": ..., "url": ...}] on the new-tab page
    "kept_tabs": [],               # addresses of Keep tabs: reopened at launch, kept through Burn
    "hidden": {},                  # site -> CSS selectors of items you hid with the picker
}

HARDENED = {"protection": "strict", "block_ads": True, "easyprivacy": True, "hide_cookie_banners": True,
            "strip_tracking": True, "https_only": True, "gpc": True, "clear_on_quit": True, "generic_ua": True}


def is_hardened(settings):
    return all(settings[k] == v for k, v in HARDENED.items())


class Settings:
    def __init__(self):
        saved = read_json("settings.json", {})
        self.values = dict(DEFAULTS)
        for k, v in saved.items():
            if k in DEFAULTS and type(v) is type(DEFAULTS[k]):
                self.values[k] = v
        self.listeners = []

    def __getitem__(self, key):
        return self.values[key]

    def __setitem__(self, key, value):
        if self.values.get(key) == value:
            return
        self.values[key] = value
        write_json("settings.json", self.values)
        for fn in list(self.listeners):
            fn(key)

    def on_change(self, fn):
        self.listeners.append(fn)
