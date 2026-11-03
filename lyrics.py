import json
import os
import re
import subprocess
import sys
import threading
import time

import requests

# ---- Configuration ----
POLL_SECONDS = 0.25
BAR_HEIGHT = 36
BAR_BOTTOM_MARGIN = 0  # Raise this if it overlaps a dock or taskbar
LYRIC_OFFSET = 0.0     # Seconds. Positive = show lyrics earlier, negative = later

MPRIS_PREFIX = "org.mpris.MediaPlayer2."
MPRIS_PATH = "/org/mpris/MediaPlayer2"
MPRIS_PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
# Video players like totem are ignored. playerctld mirrors whichever player was last active,
# so it works as a fallback when reading Spotify/Brave directly fails.
PLAYER_PREFERENCE = ("spotify", "brave", "chrom", "firefox", "playerctld")

LRC_LINE = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\](.*)")
HEADERS = {"User-Agent": "spotify-lyrics-overlay-script/1.2"}
_seen_errors = {}  # so the same debug message is only printed once


# ---------------------------------------------------------------- lyrics

def parse_lrc(lrc_text):
    """Turn raw LRC text into a sorted list of (timestamp_seconds, line) tuples."""
    lines = []
    for raw in lrc_text.splitlines():
        match = LRC_LINE.match(raw)
        if not match:
            continue
        minutes, seconds, text = match.groups()
        lines.append((int(minutes) * 60 + float(seconds), text.strip()))
    lines.sort(key=lambda pair: pair[0])
    return lines


def fetch_synced_lyrics(track_name, artist_name, album_name, duration_sec):
    """Look up synced lyrics on LRCLIB: exact match first, then search fallbacks."""
    clean_track = re.sub(r"\s*[-—(\[].*$", "", track_name).strip() or track_name
    clean_artist = artist_name.split(",")[0].split(";")[0].strip()

    try:
        resp = requests.get(
            "https://lrclib.net/api/get",
            params={
                "track_name": track_name,
                "artist_name": clean_artist,
                "album_name": album_name,
                "duration": duration_sec,
            },
            headers=HEADERS,
            timeout=6,
        )
        if resp.status_code == 200 and resp.json().get("syncedLyrics"):
            return parse_lrc(resp.json()["syncedLyrics"])
    except (requests.RequestException, ValueError):
        pass

    attempts = [
        {"q": f"{clean_track} {clean_artist}"},
        {"track_name": clean_track, "artist_name": clean_artist},
    ]
    for params in attempts:
        try:
            resp = requests.get(
                "https://lrclib.net/api/search", params=params, headers=HEADERS, timeout=6
            )
            if resp.status_code != 200:
                continue
            results = resp.json()
            results.sort(key=lambda r: abs((r.get("duration") or 0) - duration_sec))
            for result in results:
                if result.get("syncedLyrics"):
                    return parse_lrc(result["syncedLyrics"])
        except (requests.RequestException, ValueError):
            continue
    return []


def index_for_position(lines, position_sec):
    """Return the index of the last line whose timestamp has already passed."""
    idx = -1
    for i, (timestamp, _) in enumerate(lines):
        if timestamp <= position_sec:
            idx = i
        else:
            break
    return idx


# ---------------------------------------------------------------- D-Bus (MPRIS) via busctl

def run(cmd, timeout=3):
    return subprocess.check_output(cmd, stderr=subprocess.PIPE, timeout=timeout).decode("utf-8").strip()


def list_players():
    """Names of MPRIS players on the session bus, without the org.mpris prefix."""
    out = run(["busctl", "--user", "list", "--no-legend", "--no-pager"])
    players = []
    for line in out.splitlines():
        name = line.split()[0] if line.split() else ""
        if name.startswith(MPRIS_PREFIX):
            players.append(name[len(MPRIS_PREFIX):])
    return players


def get_prop(player, prop):
    out = run([
        "busctl", "--user", "--json=short", "get-property",
        MPRIS_PREFIX + player, MPRIS_PATH, MPRIS_PLAYER_IFACE, prop,
    ])
    return json.loads(out)["data"]


def unwrap(meta, key, default=None):
    """Metadata values arrive as {'type': .., 'data': ..}."""
    item = meta.get(key)
    return item["data"] if isinstance(item, dict) and "data" in item else default


def pick_player(players):
    """Choose a player that is playing; prefer Spotify, then browsers."""
    statuses = {}
    for p in players:
        try:
            statuses[p] = get_prop(p, "PlaybackStatus")
        except subprocess.CalledProcessError as e:
            msg = (e.stderr or b"").decode("utf-8", "ignore").strip()
            statuses[p] = "ERR:" + (msg[-70:] or f"exit {e.returncode}")
            if _seen_errors.get(p) != msg:
                _seen_errors[p] = msg
                print(f"[debug] {p}: {msg}")
        except Exception as e:
            statuses[p] = f"ERR:{type(e).__name__} {str(e)[:40]}"
    playing = [p for p in players if statuses[p] == "Playing"]
    for group in (playing, players):
        for key in PLAYER_PREFERENCE:
            for p in group:
                if key in p.lower() and not statuses[p].startswith("ERR"):
                    return p, statuses
    return None, statuses


def get_local_playback():
    """Returns (playback_dict, error_message). Exactly one is None."""
    try:
        players = list_players()
        if not players:
            return None, "No media player found on D-Bus (open Spotify and press play)"

        player, statuses = pick_player(players)
        summary = ", ".join(f"{p}={s}" for p, s in statuses.items())
        if not player:
            return None, f"Can't read any player ({summary})"

        meta = get_prop(player, "Metadata")
        title = unwrap(meta, "xesam:title", "")
        if not title:
            return None, f"{player}: no track loaded ({summary})"

        artists = unwrap(meta, "xesam:artist", [])
        artist = artists[0] if isinstance(artists, list) and artists else (artists or "")
        album = unwrap(meta, "xesam:album", "") or ""
        length_us = unwrap(meta, "mpris:length", 0) or 0

        try:
            position_sec = int(get_prop(player, "Position")) / 1_000_000
        except Exception:
            position_sec = 0.0

        return {
            "track_id": f"{title}-{artist}",
            "title": title,
            "artist": artist,
            "album": album,
            "duration_sec": round(int(length_us) / 1_000_000),
            "position_sec": position_sec,
            "playing": statuses.get(player) == "Playing",
        }, None

    except FileNotFoundError:
        return None, "busctl not found (needs systemd): try playerctl-free setup"
    except subprocess.CalledProcessError as e:
        err = (e.stderr or b"").decode("utf-8", "ignore").strip()
        print(f"[debug] busctl failed: {err!r}")
        return None, f"D-Bus error: {err[:80] or 'unknown'}"
    except subprocess.TimeoutExpired:
        return None, "Player did not respond (timed out)"
    except Exception as e:
        return None, f"Error: {e}"


# ---------------------------------------------------------------- state / threads

class LyricsState:
    """Thread-safe state manager for the lyrics data layer."""

    def __init__(self):
        self._lock = threading.Lock()
        self._text = "Waiting for Spotify playback..."
        self.active = False  # True while a player is found (used by the top-level bar extension)

    def set(self, text):
        with self._lock:
            self._text = text

    def get(self):
        with self._lock:
            return self._text


def poll_loop(state: LyricsState, stop_event: threading.Event):
    current_track_id = None
    lines = []
    last_text = None
    fetch_token = 0

    def load_lyrics(token, playback):
        nonlocal lines
        result = fetch_synced_lyrics(
            playback["title"], playback["artist"], playback["album"], playback["duration_sec"]
        )
        if token == fetch_token:  # ignore if the track changed meanwhile
            lines = result
            if not result:
                state.set(f"{playback['title']} - {playback['artist']} (no synced lyrics found)")

    while not stop_event.is_set():
        playback, error = get_local_playback()

        if not playback:
            state.active = False
            if last_text != error:
                state.set(error)
                last_text = error
            current_track_id = None
            lines = []
            time.sleep(1)
            continue

        state.active = True
        if playback["track_id"] != current_track_id:
            current_track_id = playback["track_id"]
            lines = []
            last_text = None
            fetch_token += 1
            state.set(f"{playback['title']} - {playback['artist']}")
            threading.Thread(
                target=load_lyrics, args=(fetch_token, playback), daemon=True
            ).start()

        if lines:
            idx = index_for_position(lines, playback["position_sec"] + LYRIC_OFFSET)
            if idx >= 0:
                text = lines[idx][1] or "♪"
                if text != last_text:
                    last_text = text
                    state.set(text)

        time.sleep(POLL_SECONDS)


# ---------------------------------------------------------------- overlay

def build_overlay(state: LyricsState, stop_event: threading.Event):
    import tkinter as tk  # only needed for the old floating-window mode

    root = tk.Tk()
    try:
        # Undecorated panel-style window; the desktop treats it like a taskbar
        root.attributes("-type", "dock")
    except tk.TclError:
        root.overrideredirect(True)    # Fallback: borderless but will overlap windows
    root.attributes("-topmost", True)
    try:
        root.attributes("-alpha", 0.85)
    except tk.TclError:
        pass
    root.config(bg="black")

    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()
    y = screen_h - BAR_HEIGHT - BAR_BOTTOM_MARGIN
    bar_w = screen_w                     # full screen width
    x = 0
    root.geometry(f"{bar_w}x{BAR_HEIGHT}+{x}+{y}")
    root.update()

    def reserve_space():
        """Tell the window manager to keep other windows out of the bar's area (a 'strut')."""
        try:
            wid = hex(int(root.wm_frame(), 16))
            h = BAR_HEIGHT + BAR_BOTTOM_MARGIN
            for prop, value in (
                ("_NET_WM_STRUT_PARTIAL", f"0,0,0,{h},0,0,0,0,0,0,{x},{x + bar_w - 1}"),
                ("_NET_WM_STRUT", f"0,0,0,{h}"),
            ):
                subprocess.run(
                    ["xprop", "-id", wid, "-f", prop, "32c", "-set", prop, value],
                    check=True, capture_output=True, timeout=3,
                )
        except Exception as e:
            print(f"[debug] could not reserve screen space: {e}")

    root.after(300, reserve_space)  # after the window is on screen

    label = tk.Label(
        root,
        text="",
        font=("Sans", 14, "bold"),
        fg="white",
        bg="black",
        wraplength=bar_w - 80,
    )
    label.pack(expand=True, fill="both", padx=20, pady=2)

    close_btn = tk.Label(root, text="✕", font=("Sans", 12), fg="gray", bg="black", cursor="hand2")
    close_btn.place(relx=1.0, rely=0.0, anchor="ne", x=-10, y=5)

    def on_close(_event=None):
        stop_event.set()
        root.destroy()

    close_btn.bind("<Button-1>", on_close)
    root.bind("<Escape>", on_close)

    def refresh():
        label.config(text=state.get())
        if not stop_event.is_set():
            root.after(100, refresh)

    refresh()
    root.mainloop()


def write_state_loop(state: LyricsState, stop_event: threading.Event):
    """Daemon mode: publish the current lyric to a file for the GNOME extension to draw."""
    cache = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    folder = os.path.join(cache, "spotify-lyrics")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "state.json")
    tmp = path + ".tmp"

    last = None
    last_write = 0.0
    while not stop_event.is_set():
        current = (state.active, state.get())
        now = time.time()
        # Write on change, and every 2s as a heartbeat so the extension knows we're alive
        if current != last or now - last_write >= 2:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"active": current[0], "text": current[1], "ts": now}, f)
            os.replace(tmp, path)
            last, last_write = current, now
        time.sleep(0.1)


def main():
    state = LyricsState()
    stop_event = threading.Event()

    poll_thread = threading.Thread(target=poll_loop, args=(state, stop_event), daemon=True)
    poll_thread.start()

    if "--daemon" in sys.argv:
        try:
            write_state_loop(state, stop_event)
        except KeyboardInterrupt:
            stop_event.set()
        return

    build_overlay(state, stop_event)


if __name__ == "__main__":
    main()
