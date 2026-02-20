#!/bin/sh
# Install recollfox: index Firefox history with Recoll
set -e

REPO="https://raw.githubusercontent.com/blemli/recollfox/main"
INSTALL_DIR="$HOME/.local/share/recollfox"
SCRIPT="$INSTALL_DIR/recollfox.py"

echo "Installing recollfox..."

# Download export script
mkdir -p "$INSTALL_DIR"
curl -fsSL "$REPO/recollfox.py" -o "$SCRIPT"
chmod +x "$SCRIPT"

# Enable Recoll web queue indexing
mkdir -p "$HOME/.recoll"
if ! grep -q "processwebqueue" "$HOME/.recoll/recoll.conf" 2>/dev/null; then
    echo "processwebqueue = 1" >> "$HOME/.recoll/recoll.conf"
fi

# Install cron job (skip if already present)
if ! crontab -l 2>/dev/null | grep -q recollfox; then
    (crontab -l 2>/dev/null; echo "* * * * * $SCRIPT") | crontab -
fi

# Initial export
"$SCRIPT"

echo "Done! Firefox history will be indexed by Recoll every minute."
