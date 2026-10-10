// WENV Today: a quiet day/year progress line and task list on the desktop background.
// Logic lives in store.js (unit-tested); this file only draws it and saves changes.
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import Soup from 'gi://Soup?version=3.0';
import St from 'gi://St';
import Meta from 'gi://Meta';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as ExtensionUtils from 'resource:///org/gnome/shell/misc/extensionUtils.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Store from './store.js';
import {ScanoutGuard} from './scanout.js';

const FILTER_UUID = 'colorblind-filters@G-dH.github.com';

const REFRESH_SECONDS = 30;
const ACTIVITY_SECONDS = 60;
const AW_URL = 'http://127.0.0.1:5600/api/0';

export default class WenvToday extends Extension {
    enable() {
        this._dataDir = GLib.build_filenamev([GLib.get_user_data_dir(), 'wenv-today']);
        this._dataFile = GLib.build_filenamev([this._dataDir, 'tasks.json']);
        this._state = this._load();
        this._expanded = false;
        this._activeSeconds = null;
        this._afkBucket = null;
        this._session = new Soup.Session();

        this._buildUi();
        this._place();
        this._monitorsId = Main.layoutManager.connect('monitors-changed', () => this._place());
        this._refreshId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, REFRESH_SECONDS, () => {
            this._refresh();
            return GLib.SOURCE_CONTINUE;
        });
        this._activityId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, ACTIVITY_SECONDS, () => {
            this._fetchActivity();
            return GLib.SOURCE_CONTINUE;
        });
        this._rebuildList();
        this._refresh();
        this._fetchActivity();

        // While the grayscale filter is on, stop mutter from scanning a window out directly (see scanout.js).
        try {
            this._scanout = new ScanoutGuard({
                extensionManager: Main.extensionManager,
                filterUuid: FILTER_UUID,
                activeState: ExtensionUtils.ExtensionState.ACTIVE,
                inhibit: () => Meta.disable_unredirect_for_display(global.display),
                release: () => Meta.enable_unredirect_for_display(global.display),
            });
            this._scanout.enable();
        } catch (e) {
            console.error(`wenv-today: scanout guard not started: ${e}`);
            this._scanout = null;
        }
    }

    disable() {
        if (this._scanout) {
            this._scanout.disable();
            this._scanout = null;
        }
        if (this._refreshId) {
            GLib.source_remove(this._refreshId);
            this._refreshId = 0;
        }
        if (this._activityId) {
            GLib.source_remove(this._activityId);
            this._activityId = 0;
        }
        if (this._monitorsId) {
            Main.layoutManager.disconnect(this._monitorsId);
            this._monitorsId = 0;
        }
        if (this._container) {
            this._container.destroy();
            this._container = null;
        }
        this._session = null;
        this._state = null;
    }

    // ---- storage -------------------------------------------------------

    _load() {
        const file = Gio.File.new_for_path(this._dataFile);
        if (!file.query_exists(null))
            return Store.emptyState();
        try {
            const [, contents] = file.load_contents(null);
            const {state, error} = Store.parseState(new TextDecoder().decode(contents));
            if (error) {
                // Keep the unreadable file so no task is lost, then start from an empty list.
                const stamp = new Date().toISOString().replace(/[:.]/g, '-');
                file.copy(Gio.File.new_for_path(GLib.build_filenamev([this._dataDir, `tasks.corrupt-${stamp}.json`])),
                    Gio.FileCopyFlags.NONE, null, null);
                console.error(`wenv-today: tasks file unreadable (${error}); copied aside`);
            }
            return state;
        } catch (e) {
            console.error(`wenv-today: could not read tasks: ${e}`);
            return Store.emptyState();
        }
    }

    _save() {
        try {
            GLib.mkdir_with_parents(this._dataDir, 0o755);
            Gio.File.new_for_path(this._dataFile).replace_contents(
                new TextEncoder().encode(Store.serializeState(this._state)),
                null, false, Gio.FileCreateFlags.NONE, null);
        } catch (e) {
            console.error(`wenv-today: could not save tasks: ${e}`);
        }
    }

    _mutate(fn) {
        this._state = fn(this._state);
        this._save();
        this._rebuildList();
        this._refresh();
    }

    // ---- activity (ActivityWatch AFK bucket, optional) -------------------

    _fetchActivity() {
        if (!this._session)
            return;
        if (this._afkBucket) {
            this._queryAfk();
            return;
        }
        this._getText(`${AW_URL}/buckets/`, text => {
            const name = Object.keys(JSON.parse(text)).find(n => n.startsWith('aw-watcher-afk_'));
            if (name) {
                this._afkBucket = name;
                this._queryAfk();
            }
        });
    }

    _queryAfk() {
        const now = new Date();
        const dayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        const url = `${AW_URL}/buckets/${encodeURIComponent(this._afkBucket)}/events` +
            `?start=${encodeURIComponent(dayStart.toISOString())}&end=${encodeURIComponent(now.toISOString())}&limit=-1`;
        this._getText(url, text => {
            this._activeSeconds = Store.activeSecondsToday(JSON.parse(text), new Date());
            this._refresh();
        });
    }

    _getText(url, onText) {
        const message = Soup.Message.new('GET', url);
        this._session.send_and_read_async(message, GLib.PRIORITY_DEFAULT, null, (session, result) => {
            try {
                const bytes = session.send_and_read_finish(result);
                if (message.get_status() !== Soup.Status.OK)
                    throw new Error(`HTTP ${message.get_status()}`);
                onText(new TextDecoder().decode(bytes.get_data()));
            } catch (e) {
                // ActivityWatch not running: the widget simply omits the active-time figure.
                this._activeSeconds = null;
                this._refresh();
            }
        });
    }

    // ---- UI ------------------------------------------------------------

    _buildUi() {
        this._container = new St.Widget({layout_manager: new Clutter.BinLayout(), reactive: false});
        this._box = new St.BoxLayout({vertical: true, style_class: 'wenv-today', reactive: true});
        this._box.x_align = Clutter.ActorAlign.CENTER;
        this._box.y_align = Clutter.ActorAlign.START;
        this._container.add_child(this._box);

        this._header = new St.BoxLayout({vertical: true, reactive: true});
        this._dayLabel = new St.Label({style_class: 'wenv-today-title'});
        this._nextLabel = new St.Label({style_class: 'wenv-today-next'});
        this._countLabel = new St.Label({style_class: 'wenv-today-muted'});
        this._header.add_child(this._dayLabel);
        this._header.add_child(this._nextLabel);
        this._header.add_child(this._countLabel);
        this._header.connect('button-press-event', () => {
            this._setExpanded(!this._expanded);
            return Clutter.EVENT_STOP;
        });
        this._box.add_child(this._header);

        this._body = new St.BoxLayout({vertical: true, visible: false});
        this._list = new St.BoxLayout({vertical: true});
        this._entry = new St.Entry({
            style_class: 'wenv-today-entry',
            hint_text: 'add a task',
            can_focus: true,
            reactive: true,
        });
        this._entry.clutter_text.connect('activate', () => {
            const text = this._entry.get_text();
            this._entry.set_text('');
            this._mutate(st => Store.addTask(st, text, new Date()));
            this._entry.grab_key_focus();
            return Clutter.EVENT_STOP;
        });
        this._body.add_child(this._list);
        this._body.add_child(this._entry);
        this._box.add_child(this._body);

        Main.layoutManager._backgroundGroup.add_child(this._container);
    }

    _place() {
        if (!this._container)
            return;
        const monitor = Main.layoutManager.primaryMonitor;
        this._container.set_position(monitor.x, monitor.y);
        this._container.set_size(monitor.width, monitor.height);
        this._box.margin_top = Math.round(monitor.height * 0.28);
    }

    _setExpanded(expanded) {
        this._expanded = expanded;
        this._body.visible = expanded;
        this._rebuildList();
        if (expanded)
            this._entry.grab_key_focus();
    }

    _rebuildList() {
        if (!this._list || !this._state)
            return;
        this._list.destroy_all_children();
        const now = new Date();
        const s = Store.summary(this._state, now);
        for (const t of s.open)
            this._list.add_child(this._row(`○  ${t.text}`, 'wenv-today-row', () =>
                this._mutate(st => Store.toggleTask(st, t.id, new Date()))));
        for (const t of s.doneToday)
            this._list.add_child(this._row(`●  ${t.text}`, 'wenv-today-row wenv-today-row-done', () =>
                this._mutate(st => Store.toggleTask(st, t.id, new Date()))));
    }

    _row(label, styleClass, onClick) {
        const button = new St.Button({label, style_class: styleClass, x_align: Clutter.ActorAlign.START, reactive: true});
        button.connect('clicked', onClick);
        return button;
    }

    _refresh() {
        if (!this._dayLabel || !this._state)
            return;
        const now = new Date();
        const p = Store.percentLeft(now);
        const s = Store.summary(this._state, now);
        this._dayLabel.text = `Day ${p.day}% left   ·   Year ${p.year}% left`;
        this._nextLabel.text = s.next ? `Next: ${s.next.text}` : 'Nothing open. Add a task.';
        const active = this._activeSeconds === null ? '' : `   ·   ${Store.formatDuration(this._activeSeconds)} active today`;
        this._countLabel.text = `${s.openCount} left   ·   ${s.doneTodayCount} done today${active}`;
    }
}
