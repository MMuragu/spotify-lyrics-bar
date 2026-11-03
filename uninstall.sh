#!/usr/bin/env bash
# Removes the Spotify lyrics bar completely.
UUID="lyric-bar@local"

pkill -f "spotify-lyrics/lyrics.py --daemon" 2>/dev/null || true

gnome-extensions disable "$UUID" 2>/dev/null || true
rm -rf "$HOME/.local/share/gnome-shell/extensions/$UUID"
rm -rf "$HOME/.local/share/spotify-lyrics"
rm -rf "$HOME/.cache/spotify-lyrics"
rm -f "${XDG_CONFIG_HOME:-$HOME/.config}/autostart/spotify-lyrics.desktop"
# Your colour settings are kept; delete ~/.config/spotify-lyrics yourself if you want them gone.

echo "Removed. Log out and back in to finish."
