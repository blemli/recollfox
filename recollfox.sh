#!/usr/bin/env python3
"""recollfox — Export Firefox browsing history for Recoll web indexing.
Runs from cron every minute; only exports new entries since last run.
"""

import glob
import hashlib
import html
import os
import sqlite3
import sys

RECOLL_WEBQUEUE = os.environ.get("RECOLL_WEBQUEUE", os.path.expanduser("~/.recollweb/ToIndex"))
STATE_FILE = os.path.expanduser("~/.local/share/recollfox/last_visit_date")
FIREFOX_PROFILE_DIRS = [
    os.path.expanduser("~/Library/Application Support/Firefox/Profiles"),  # macOS
    os.path.expanduser("~/.mozilla/firefox"),  # Linux
    os.path.expanduser("~/snap/firefox/common/.mozilla/firefox"),  # Ubuntu snap
    os.path.expanduser("~/.var/app/org.mozilla.firefox/.mozilla/firefox"),  # Flatpak
]


def find_active_profile():
    """Find the Firefox profile with the most recent visit."""
    best_db, best_ts = None, 0
    for profile_dir in FIREFOX_PROFILE_DIRS:
        for db in glob.glob(os.path.join(profile_dir, "*/places.sqlite")):
            try:
                conn = sqlite3.connect(f"file:{db}?immutable=1", uri=True)
                ts = conn.execute("SELECT COALESCE(MAX(last_visit_date),0) FROM moz_places").fetchone()[0]
                conn.close()
                if ts > best_ts:
                    best_ts, best_db = ts, db
            except sqlite3.Error:
                continue
    return best_db


def main():
    places_db = find_active_profile()
    if not places_db:
        print("No Firefox profile found", file=sys.stderr)
        sys.exit(1)

    os.makedirs(RECOLL_WEBQUEUE, exist_ok=True)
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)

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
    max_ts = last_ts
    for url, title, visit_date, description in rows:
        if not url:
            continue
        h = hashlib.md5(url.encode()).hexdigest()

        # Recoll web queue format: paired files in the queue directory.
        # _<hash> = metadata dot file  (url, hit type, mime type, then t:key = value)
        # <hash>  = content file       (HTML)

        with open(os.path.join(RECOLL_WEBQUEUE, f"_{h}"), "w") as f:
            f.write(f"{url}\nWebHistory\ntext/html\nt:title = {title}\n")

        t, d, u = html.escape(title), html.escape(description), html.escape(url)
        with open(os.path.join(RECOLL_WEBQUEUE, f"{h}"), "w") as f:
            f.write(
                f"<html><head><meta charset=\"utf-8\"><title>{t}</title></head>"
                f"<body><h1>{t}</h1><p>{d}</p><a href=\"{u}\">{u}</a></body></html>\n"
            )

        if visit_date > max_ts:
            max_ts = visit_date
        count += 1

    if max_ts > last_ts:
        with open(STATE_FILE, "w") as f:
            f.write(str(max_ts))

    print(f"Exported {count} entries to {RECOLL_WEBQUEUE}")


if __name__ == "__main__":
    main()
