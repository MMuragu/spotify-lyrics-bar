import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import St from 'gi://St';
import Clutter from 'gi://Clutter';
import Pango from 'gi://Pango';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const STATE_DIR = GLib.build_filenamev([GLib.get_user_cache_dir(), 'spotify-lyrics']);
const STATE_PATH = GLib.build_filenamev([STATE_DIR, 'state.json']);
const CONFIG_DIR = GLib.build_filenamev([GLib.get_user_config_dir(), 'spotify-lyrics']);
const CONFIG_PATH = GLib.build_filenamev([CONFIG_DIR, 'config.json']);

// If the lyrics program stops updating the state file, hide the bar after this long.
const STALE_SECONDS = 6;

const DEFAULTS = {
    background: 'rgba(0, 0, 0, 0.85)',
    color: '#ffffff',
    font_size: 14,
    height: 36,
    bold: true,
};

function readJson(path) {
    try {
        const [ok, bytes] = GLib.file_get_contents(path);
        if (!ok)
            return null;
        return JSON.parse(new TextDecoder().decode(bytes));
    } catch (e) {
        return null;
    }
}

export default class SpotifyLyricsExtension extends Extension {
    enable() {
        GLib.mkdir_with_parents(STATE_DIR, 0o755);
        GLib.mkdir_with_parents(CONFIG_DIR, 0o755);

        this._cfg = {...DEFAULTS};
        this._state = {active: false, text: '', ts: 0};

        this._bar = new St.BoxLayout({reactive: false, visible: false});
        this._label = new St.Label({
            text: '',
            x_align: Clutter.ActorAlign.CENTER,
            y_align: Clutter.ActorAlign.CENTER,
            x_expand: true,
        });
        this._label.clutter_text.ellipsize = Pango.EllipsizeMode.END;
        this._bar.add_child(this._label);

        // affectsStruts is what makes maximized windows stop above the bar
        Main.layoutManager.addChrome(this._bar, {
            affectsStruts: true,
            trackFullscreen: true,
        });

        this._monitorsId = Main.layoutManager.connect('monitors-changed', () => this._place());

        this._stateMonitor = Gio.File.new_for_path(STATE_PATH)
            .monitor_file(Gio.FileMonitorFlags.NONE, null);
        this._stateMonitorId = this._stateMonitor.connect('changed', () => this._loadState());

        this._configMonitor = Gio.File.new_for_path(CONFIG_PATH)
            .monitor_file(Gio.FileMonitorFlags.NONE, null);
        this._configMonitorId = this._configMonitor.connect('changed', () => this._loadConfig());

        this._timerId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 2, () => {
            this._refresh();
            return GLib.SOURCE_CONTINUE;
        });

        this._loadConfig();
        this._loadState();
    }

    disable() {
        if (this._timerId) {
            GLib.Source.remove(this._timerId);
            this._timerId = null;
        }
        if (this._stateMonitor) {
            this._stateMonitor.disconnect(this._stateMonitorId);
            this._stateMonitor.cancel();
            this._stateMonitor = null;
        }
        if (this._configMonitor) {
            this._configMonitor.disconnect(this._configMonitorId);
            this._configMonitor.cancel();
            this._configMonitor = null;
        }
        if (this._monitorsId) {
            Main.layoutManager.disconnect(this._monitorsId);
            this._monitorsId = null;
        }
        if (this._bar) {
            Main.layoutManager.removeChrome(this._bar);
            this._bar.destroy();
            this._bar = null;
            this._label = null;
        }
    }

    _loadConfig() {
        const user = readJson(CONFIG_PATH);
        this._cfg = {...DEFAULTS, ...(user ?? {})};

        const bold = this._cfg.bold ? 'bold' : 'normal';
        this._bar.set_style(`background-color: ${this._cfg.background};`);
        this._label.set_style(
            `color: ${this._cfg.color}; font-size: ${this._cfg.font_size}pt; font-weight: ${bold};`);

        this._place();
    }

    _loadState() {
        const data = readJson(STATE_PATH);
        if (data)
            this._state = data;
        this._refresh();
    }

    _refresh() {
        if (!this._bar)
            return;
        const age = Date.now() / 1000 - (this._state.ts ?? 0);
        const fresh = age < STALE_SECONDS;
        const show = fresh && this._state.active && !!this._state.text;

        this._label.text = this._state.text ?? '';
        this._bar.visible = show;
    }

    _place() {
        if (!this._bar)
            return;
        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor)
            return;
        const h = Math.max(20, Number(this._cfg.height) || DEFAULTS.height);
        this._bar.set_position(monitor.x, monitor.y + monitor.height - h);
        this._bar.set_size(monitor.width, h);
    }
}
