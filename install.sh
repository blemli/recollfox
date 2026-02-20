#!/bin/sh
# Install firefox-recoll: index Firefox history with Recoll
set -e

REPO="https://raw.githubusercontent.com/blemli/firefox-recoll/main"
INSTALL_DIR="$HOME/.local/share/firefox-recoll"
SCRIPT="$INSTALL_DIR/firefox-history-export.sh"

echo "Installing firefox-recoll..."

# Download export script
mkdir -p "$INSTALL_DIR"
curl -fsSL "$REPO/firefox-history-export.sh" -o "$SCRIPT"
chmod +x "$SCRIPT"

# Enable Recoll web queue indexing
mkdir -p "$HOME/.recoll"
if ! grep -q "processwebqueue" "$HOME/.recoll/recoll.conf" 2>/dev/null; then
    echo "processwebqueue = 1" >> "$HOME/.recoll/recoll.conf"
fi

# Install cron job (skip if already present)
if ! crontab -l 2>/dev/null | grep -q firefox-history-export; then
    (crontab -l 2>/dev/null; echo "* * * * * $SCRIPT") | crontab -
fi

# Initial export
"$SCRIPT"

echo "Done! Firefox history will be indexed by Recoll every minute."
