#!/usr/bin/env python3
"""recollfox — Export Firefox browsing history for Recoll web indexing.
Runs from cron every minute; only exports new entries since last run.
"""

import configparser
import fcntl
import hashlib
import html
import ipaddress
import os
import socket
import sqlite3
import sys
import urllib.error
import urllib.request
from html.parser import HTMLParser
from urllib.parse import urlsplit

RECOLL_WEBQUEUE = os.environ.get("RECOLL_WEBQUEUE", os.path.expanduser("~/.recollweb/ToIndex"))
STATE_FILE = os.path.expanduser("~/.local/share/recollfox/last_visit_date")
# Page fetching: cron runs every minute, so keep each run small.
FETCH_LIMIT = int(os.environ.get("RECOLLFOX_FETCH_MAX", "20"))  # page fetches per run
FETCH_TIMEOUT = 5  # seconds per page
FETCH_MAX_BYTES = 512 * 1024  # read at most this much of each page
FIREFOX_DIRS = [
    os.path.expanduser("~/Library/Application Support/Firefox"),  # macOS
    os.path.expanduser("~/.mozilla/firefox"),  # Linux
    os.path.expanduser("~/snap/firefox/common/.mozilla/firefox"),  # Ubuntu snap
    os.path.expanduser("~/.var/app/org.mozilla.firefox/.mozilla/firefox"),  # Flatpak
    os.path.expanduser("~/.var/app/org.mozilla.firefox/config/mozilla/firefox"),  # Flatpak
]


def find_default_profile():
    """Find Firefox's default profile by parsing profiles.ini."""
    for firefox_dir in FIREFOX_DIRS:
        ini_path = os.path.join(firefox_dir, "profiles.ini")
        if not os.path.isfile(ini_path):
            continue

        cfg = configparser.ConfigParser()
        cfg.read(ini_path)

        # Install* sections have the actively used default profile
        for section in cfg.sections():
            if section.startswith("Install") and cfg.has_option(section, "Default"):
                path = cfg.get(section, "Default")
                if not os.path.isabs(path):
                    path = os.path.join(firefox_dir, path)
                db = os.path.join(path, "places.sqlite")
                if os.path.isfile(db):
                    return db

        # Fallback: look for a Profile section with Default=1
        for section in cfg.sections():
            if section.startswith("Profile") and cfg.get(section, "Default", fallback="") == "1":
                path = cfg.get(section, "Path")
                if not os.path.isabs(path):
                    path = os.path.join(firefox_dir, path)
                db = os.path.join(path, "places.sqlite")
                if os.path.isfile(db):
                    return db

    return None


class TextExtractor(HTMLParser):
    """Collects the visible text of an HTML document."""

    SKIP = {"script", "style", "noscript", "template", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)

    def text(self):
        return " ".join(" ".join(self.parts).split())


def is_fetchable(url):
    """Only http(s) URLs whose host resolves exclusively to public addresses."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    host = parts.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith((".local", ".localhost", ".internal")):
        return False
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError, ValueError):
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global:  # private, loopback, link-local, multicast, reserved, ...
            return False
    return bool(infos)


class PublicOnlyRedirect(urllib.request.HTTPRedirectHandler):
    """Re-check every redirect target, so a public URL cannot bounce into the LAN."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not is_fetchable(newurl):
            raise urllib.error.URLError(f"redirect to non-public address refused: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


OPENER = urllib.request.build_opener(PublicOnlyRedirect)


def fetch_text(url):
    """Download a page and return its visible text, or '' on any failure.
    The caller must check is_fetchable(url) first."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; recollfox)", "Accept": "text/html"},
    )
    try:
        with OPENER.open(req, timeout=FETCH_TIMEOUT) as resp:
            if "html" not in resp.headers.get("Content-Type", ""):
                return ""
            raw = resp.read(FETCH_MAX_BYTES)
            charset = resp.headers.get_content_charset() or "utf-8"
    except Exception:
        return ""
    try:
        document = raw.decode(charset, "replace")
    except LookupError:
        document = raw.decode("utf-8", "replace")
    parser = TextExtractor()
    try:
        parser.feed(document)
    except Exception:
        return ""
    return parser.text()


def main():
    places_db = find_default_profile()
    if not places_db:
        print("No Firefox profile found", file=sys.stderr)
        sys.exit(1)

    os.makedirs(RECOLL_WEBQUEUE, exist_ok=True)
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)

    # Skip this run if the previous one is still fetching pages
    lock = open(STATE_FILE + ".lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("Previous run still in progress", file=sys.stderr)
        return

    # Read last exported timestamp (Firefox uses microseconds since epoch)
    last_ts = 0
    try:
        with open(STATE_FILE) as f:
            last_ts = int(f.read().strip())
    except (FileNotFoundError, ValueError):
        pass

    conn = sqlite3.connect(f"file:{places_db}?immutable=1", uri=True)
    conn.text_factory = lambda b: b.decode("utf-8", "replace")
    rows = conn.execute(
        "SELECT url, COALESCE(title,''), last_visit_date, COALESCE(description,'')"
        " FROM moz_places"
        " WHERE last_visit_date > ? AND hidden = 0 AND url NOT LIKE 'place:%'"
        " ORDER BY last_visit_date ASC",
        (last_ts,),
    ).fetchall()
    conn.close()

    count = 0
    fetches = 0
    max_ts = last_ts
    for url, title, visit_date, description in rows:
        if not url:
            continue
        body = ""
        if is_fetchable(url):
            if fetches >= FETCH_LIMIT:
                break  # the rest is picked up on the next run
            fetches += 1
            body = fetch_text(url)
        h = hashlib.md5(url.encode()).hexdigest()

        # Recoll web queue format: paired files in the queue directory.
        # _<hash> = metadata dot file  (url, hit type, mime type, then t:key = value)
        # <hash>  = content file       (HTML)

        with open(os.path.join(RECOLL_WEBQUEUE, f"_{h}"), "w") as f:
            f.write(f"{url}\nWebHistory\ntext/html\nt:title = {title}\n")

        t, d, u, b = html.escape(title), html.escape(description), html.escape(url), html.escape(body)
        with open(os.path.join(RECOLL_WEBQUEUE, f"{h}"), "w") as f:
            f.write(
                f"<html><head><meta charset=\"utf-8\"><title>{t}</title></head>"
                f"<body><h1>{t}</h1><p>{d}</p><div>{b}</div><a href=\"{u}\">{u}</a></body></html>\n"
            )

        max_ts = max(max_ts, visit_date)
        count += 1

    if max_ts > last_ts:
        with open(STATE_FILE, "w") as f:
            f.write(str(max_ts))

    print(f"Exported {count} entries to {RECOLL_WEBQUEUE}")


if __name__ == "__main__":
    main()
