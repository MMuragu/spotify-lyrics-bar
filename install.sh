#!/usr/bin/env bash
# Installs the Spotify lyrics bar: a background program that reads Spotify,
# plus a GNOME Shell extension that draws the bar at the bottom of the screen.
set -e

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UUID="lyric-bar@local"
APP_DIR="$HOME/.local/share/spotify-lyrics"
EXT_DIR="$HOME/.local/share/gnome-shell/extensions/$UUID"
CFG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/spotify-lyrics"
AUTOSTART_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"

mkdir -p "$APP_DIR" "$EXT_DIR" "$CFG_DIR" "$AUTOSTART_DIR"

# 1. The lyrics program
cp "$HERE/lyrics.py" "$APP_DIR/lyrics.py"

# 2. The GNOME extension
cp "$HERE/extension/metadata.json" "$HERE/extension/extension.js" "$EXT_DIR/"

# 3. Colour/size settings (never overwrite your own edits)
if [ ! -f "$CFG_DIR/config.json" ]; then
    cp "$HERE/config.json" "$CFG_DIR/config.json"
fi

# 4. Start the lyrics program automatically at every login
cat > "$AUTOSTART_DIR/spotify-lyrics.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Spotify Lyrics
Exec=python3 $APP_DIR/lyrics.py --daemon
X-GNOME-Autostart-enabled=true
NoDisplay=true
EOF

# 5. Turn the extension on for the next login
current="$(gsettings get org.gnome.shell enabled-extensions)"
if [[ "$current" != *"$UUID"* ]]; then
    if [[ "$current" == "@as []" || "$current" == "[]" ]]; then
        new="['$UUID']"
    else
        new="${current%]}, '$UUID']"
    fi
    gsettings set org.gnome.shell enabled-extensions "$new"
fi

echo
echo "Installed."
echo "Now log out and log back in (GNOME only discovers new extensions at login)."
echo "After that the bar appears by itself whenever Spotify is open."
echo
echo "Colours and size: edit $CFG_DIR/config.json (changes apply instantly)."
echo "If the bar doesn't show, check:  gnome-extensions info $UUID"
