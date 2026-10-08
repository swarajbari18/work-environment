// Exercises the exact GIO and Soup calls used by extension.js, outside the Shell.
// Run: gjs -m test_io.js   (needs ActivityWatch on 127.0.0.1:5600 for the HTTP part)
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Soup from 'gi://Soup?version=3.0';
import * as Store from './store.js';

let failures = 0;
const check = (label, ok, detail = '') => {
    print(`${ok ? 'PASS' : 'FAIL'}  ${label}${detail ? '  ' + detail : ''}`);
    if (!ok) failures++;
};

// ---- file round trip, same calls as _save/_load --------------------------
const dir = GLib.build_filenamev([GLib.get_tmp_dir(), `wenv-today-test-${GLib.random_int()}`]);
const file = Gio.File.new_for_path(GLib.build_filenamev([dir, 'tasks.json']));
GLib.mkdir_with_parents(dir, 0o755);

let st = Store.addTask(Store.emptyState(), 'Ünïcode task ✓', new Date(2026, 9, 8, 9));
st = Store.toggleTask(st, st.tasks[0].id, new Date(2026, 9, 8, 10));
file.replace_contents(new TextEncoder().encode(Store.serializeState(st)), null, false, Gio.FileCreateFlags.NONE, null);
check('file written', file.query_exists(null));

const [, contents] = file.load_contents(null);
const back = Store.parseState(new TextDecoder().decode(contents));
check('file reads back with no parse error', back.error === null, back.error ?? '');
check('task text survives (unicode)', back.state.tasks[0]?.text === 'Ünïcode task ✓');
check('done state survives', back.state.tasks[0]?.done === true);

// Overwrite keeps no stale data.
file.replace_contents(new TextEncoder().encode(Store.serializeState(Store.emptyState())), null, false, Gio.FileCreateFlags.NONE, null);
const [, c2] = file.load_contents(null);
check('overwrite replaces content', Store.parseState(new TextDecoder().decode(c2)).state.tasks.length === 0);

// Corrupt file is copied aside, as _load does.
file.replace_contents(new TextEncoder().encode('{broken'), null, false, Gio.FileCreateFlags.NONE, null);
const aside = Gio.File.new_for_path(GLib.build_filenamev([dir, 'tasks.corrupt-test.json']));
file.copy(aside, Gio.FileCopyFlags.NONE, null, null);
check('corrupt file copied aside, original kept', aside.query_exists(null) && file.query_exists(null));

// ---- HTTP round trip, same calls as _getText -----------------------------
const session = new Soup.Session();
const getText = (url) => new Promise((resolve, reject) => {
    const message = Soup.Message.new('GET', url);
    session.send_and_read_async(message, GLib.PRIORITY_DEFAULT, null, (s, result) => {
        try {
            const bytes = s.send_and_read_finish(result);
            if (message.get_status() !== Soup.Status.OK)
                throw new Error(`HTTP ${message.get_status()}`);
            resolve(new TextDecoder().decode(bytes.get_data()));
        } catch (e) {
            reject(e);
        }
    });
});

const loop = GLib.MainLoop.new(null, false);
(async () => {
    try {
        const buckets = JSON.parse(await getText('http://127.0.0.1:5600/api/0/buckets/'));
        const afk = Object.keys(buckets).find(n => n.startsWith('aw-watcher-afk_'));
        check('ActivityWatch lists an AFK bucket', !!afk, afk ?? '');
        if (afk) {
            const now = new Date();
            const dayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate());
            const url = `http://127.0.0.1:5600/api/0/buckets/${encodeURIComponent(afk)}/events` +
                `?start=${encodeURIComponent(dayStart.toISOString())}&end=${encodeURIComponent(now.toISOString())}&limit=-1`;
            const events = JSON.parse(await getText(url));
            const seconds = Store.activeSecondsToday(events, now);
            check('active today computed from live AFK events', seconds >= 0, Store.formatDuration(seconds) + ' active');
        }
    } catch (e) {
        check('ActivityWatch HTTP round trip', false, String(e));
    }

    // Unreachable server must reject, not hang or throw uncaught.
    try {
        await getText('http://127.0.0.1:9/nothing');
        check('unreachable server rejects', false);
    } catch (e) {
        check('unreachable server rejects cleanly', true, String(e).slice(0, 60));
    }

    file.delete(null);
    aside.delete(null);
    Gio.File.new_for_path(dir).delete(null);
    print(failures === 0 ? 'ALL IO CHECKS PASSED' : `${failures} IO CHECK(S) FAILED`);
    loop.quit();
})();
loop.run();
