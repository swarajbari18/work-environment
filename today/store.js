// Pure logic for the Today widget: time progress, task list and daily counts.
// No GNOME imports, so it can be unit-tested with gjs (see test_store.js).

export const STATE_VERSION = 1;

export function emptyState() {
    return {version: STATE_VERSION, tasks: []};
}

// Returns {state, error}. Corrupt or unknown files fall back to an empty list
// and report the error, so the widget never fails to draw.
export function parseState(text) {
    try {
        const data = JSON.parse(text);
        if (!data || !Array.isArray(data.tasks))
            return {state: emptyState(), error: 'tasks missing'};
        const tasks = data.tasks.filter(t =>
            t && typeof t.id === 'string' && typeof t.text === 'string' && typeof t.done === 'boolean');
        return {state: {version: STATE_VERSION, tasks}, error: null};
    } catch (e) {
        return {state: emptyState(), error: String(e)};
    }
}

export function serializeState(state) {
    return JSON.stringify({version: STATE_VERSION, tasks: state.tasks}, null, 2) + '\n';
}

function newId(now) {
    return now.getTime().toString(36) + Math.floor(Math.random() * 36 ** 4).toString(36);
}

export function addTask(state, text, now) {
    const clean = (text || '').trim();
    if (!clean)
        return state;
    const task = {id: newId(now), text: clean, done: false, createdAt: now.toISOString(), doneAt: null};
    return {version: STATE_VERSION, tasks: [...state.tasks, task]};
}

export function toggleTask(state, id, now) {
    return {
        version: STATE_VERSION,
        tasks: state.tasks.map(t => {
            if (t.id !== id)
                return t;
            return t.done
                ? {...t, done: false, doneAt: null}
                : {...t, done: true, doneAt: now.toISOString()};
        }),
    };
}

export function isSameLocalDay(a, b) {
    return a.getFullYear() === b.getFullYear() &&
        a.getMonth() === b.getMonth() &&
        a.getDate() === b.getDate();
}

export function summary(state, now) {
    const open = state.tasks.filter(t => !t.done);
    const doneToday = state.tasks.filter(t =>
        t.done && t.doneAt && isSameLocalDay(new Date(t.doneAt), now));
    return {
        open,
        doneToday,
        openCount: open.length,
        doneTodayCount: doneToday.length,
        next: open.length > 0 ? open[0] : null,
    };
}

// Share of the local day and of the calendar year that is still left, as whole percentages.
export function percentLeft(now) {
    const y = now.getFullYear();
    const dayStart = new Date(y, now.getMonth(), now.getDate());
    const dayEnd = new Date(y, now.getMonth(), now.getDate() + 1);
    const yearStart = new Date(y, 0, 1);
    const yearEnd = new Date(y + 1, 0, 1);
    const pct = (end, start) => Math.round(100 * (end - now) / (end - start));
    return {
        day: Math.min(100, Math.max(0, pct(dayEnd, dayStart))),
        year: Math.min(100, Math.max(0, pct(yearEnd, yearStart))),
    };
}

export function formatDuration(seconds) {
    const s = Math.max(0, Math.round(seconds));
    return `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}m`;
}

// ActivityWatch AFK events for today -> seconds not AFK since local midnight.
export function activeSecondsToday(afkEvents, now) {
    const dayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    let total = 0;
    for (const e of afkEvents) {
        if (!e || !e.data || e.data.status !== 'not-afk')
            continue;
        const start = Date.parse(e.timestamp);
        if (Number.isNaN(start))
            continue;
        const from = Math.max(start, dayStart);
        const to = Math.min(start + (e.duration || 0) * 1000, now.getTime());
        if (to > from)
            total += (to - from) / 1000;
    }
    return total;
}
