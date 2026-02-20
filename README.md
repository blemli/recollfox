# recollfox

Export Firefox browsing history for [Recoll](https://www.recoll.org/) to index.

A cron job runs every minute, exporting new history entries into Recoll's web queue format. Recoll indexes them on its next run.

## Install

```bash
curl -sL blem.li/recollfox | sh
```

This downloads the export script, enables Recoll's web queue indexing, sets up a cron job (every minute), and backfills your existing history.

## How it works

- Reads Firefox's `places.sqlite` (read-only, safe while Firefox is running)
- Exports each visited URL as a pair of files in `~/.recollweb/ToIndex/`:
  - `_<hash>` — metadata (URL, title, type)
  - `<hash>` — minimal HTML with title and description
- Tracks the last exported timestamp to only process new entries
- Recoll picks up the queue files on its next indexing run

## Uninstall

```bash
crontab -l | grep -v firefox-history-export | crontab -
rm -rf ~/.local/share/firefox-recoll
```
