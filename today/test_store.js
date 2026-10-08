// Run: gjs -m test_store.js
import * as S from './store.js';

let passed = 0;
const failures = [];
function test(name, fn) {
    try {
        fn();
        passed++;
    } catch (e) {
        failures.push(`${name}: ${e.message}`);
    }
}
function eq(actual, expected, what = '') {
    const a = JSON.stringify(actual), b = JSON.stringify(expected);
    if (a !== b)
        throw new Error(`${what} expected ${b} got ${a}`);
}

// Local-time constructors so the tests do not depend on the machine's timezone.
const at = (y, mo, d, h = 0, mi = 0) => new Date(y, mo, d, h, mi, 0);

test('day left at midnight is 100%', () => eq(S.percentLeft(at(2026, 9, 8, 0, 0)).day, 100));
test('day left at 06:00 is 75%', () => eq(S.percentLeft(at(2026, 9, 8, 6, 0)).day, 75));
test('day left at noon is 50%', () => eq(S.percentLeft(at(2026, 9, 8, 12, 0)).day, 50));
test('day left at 23:59 is 0%', () => eq(S.percentLeft(at(2026, 9, 8, 23, 59)).day, 0));
test('year left on 1 Jan is 100%', () => eq(S.percentLeft(at(2026, 0, 1, 0, 0)).year, 100));
test('year left on 1 Jul (noon) is about 50%', () => {
    const y = S.percentLeft(at(2026, 6, 2, 12, 0)).year;
    if (y < 49 || y > 51) throw new Error(`got ${y}`);
});
test('year left on 31 Dec is 0%', () => eq(S.percentLeft(at(2026, 11, 31, 23, 0)).year, 0));

test('add trims and keeps order', () => {
    let st = S.emptyState();
    st = S.addTask(st, '  Write section 9  ', at(2026, 9, 8, 9));
    st = S.addTask(st, 'Review diff', at(2026, 9, 8, 9, 5));
    eq(st.tasks.map(t => t.text), ['Write section 9', 'Review diff']);
});
test('add ignores empty and whitespace text', () => {
    const st = S.addTask(S.emptyState(), '   ', at(2026, 9, 8));
    eq(st.tasks.length, 0);
});
test('ids are unique across many adds', () => {
    let st = S.emptyState();
    for (let i = 0; i < 200; i++)
        st = S.addTask(st, `t${i}`, at(2026, 9, 8, 9));
    eq(new Set(st.tasks.map(t => t.id)).size, 200);
});
test('toggle marks done with timestamp, and toggling back clears it', () => {
    const now = at(2026, 9, 8, 10);
    let st = S.addTask(S.emptyState(), 'A', now);
    const id = st.tasks[0].id;
    st = S.toggleTask(st, id, now);
    eq(st.tasks[0].done, true);
    eq(st.tasks[0].doneAt, now.toISOString());
    st = S.toggleTask(st, id, now);
    eq(st.tasks[0].done, false);
    eq(st.tasks[0].doneAt, null);
});
test('toggle of unknown id changes nothing', () => {
    const st = S.addTask(S.emptyState(), 'A', at(2026, 9, 8));
    eq(S.toggleTask(st, 'nope', at(2026, 9, 8)).tasks, st.tasks);
});

test('summary counts open tasks and tasks done today only', () => {
    const today = at(2026, 9, 8, 15);
    let st = S.emptyState();
    st = S.addTask(st, 'open one', today);
    st = S.addTask(st, 'open two', today);
    st = S.addTask(st, 'done today', today);
    st = S.addTask(st, 'done yesterday', at(2026, 9, 7, 10));
    st = S.toggleTask(st, st.tasks[2].id, at(2026, 9, 8, 14));
    st = S.toggleTask(st, st.tasks[3].id, at(2026, 9, 7, 18));
    const s = S.summary(st, today);
    eq(s.openCount, 2, 'openCount');
    eq(s.doneTodayCount, 1, 'doneTodayCount');
    eq(s.next.text, 'open one', 'next');
});
test('summary with nothing open has no next task', () => {
    const s = S.summary(S.emptyState(), at(2026, 9, 8));
    eq(s.next, null);
    eq(s.openCount, 0);
});

test('parse valid state round-trips', () => {
    let st = S.addTask(S.emptyState(), 'keep me', at(2026, 9, 8));
    const r = S.parseState(S.serializeState(st));
    eq(r.error, null);
    eq(r.state.tasks.map(t => t.text), ['keep me']);
});
test('parse corrupt JSON falls back to empty with an error', () => {
    const r = S.parseState('{not json');
    eq(r.state.tasks, []);
    if (!r.error) throw new Error('expected an error');
});
test('parse drops malformed task entries but keeps good ones', () => {
    const r = S.parseState(JSON.stringify({tasks: [
        {id: 'a', text: 'good', done: false},
        {id: 'b', text: 5, done: false},
        null,
    ]}));
    eq(r.state.tasks.map(t => t.id), ['a']);
});
test('parse without a tasks array is an error', () => {
    const r = S.parseState('{"version":1}');
    eq(r.state.tasks, []);
    if (!r.error) throw new Error('expected an error');
});

test('formatDuration', () => {
    eq(S.formatDuration(7800), '2h 10m');
    eq(S.formatDuration(59), '0h 00m');
    eq(S.formatDuration(-5), '0h 00m');
});

test('activeSecondsToday counts only not-afk time since midnight', () => {
    const now = at(2026, 9, 8, 12, 0);
    const events = [
        {timestamp: at(2026, 9, 8, 9, 0).toISOString(), duration: 3600, data: {status: 'not-afk'}},
        {timestamp: at(2026, 9, 8, 10, 0).toISOString(), duration: 600, data: {status: 'afk'}},
        // Started yesterday, runs into today: only the part after midnight counts.
        {timestamp: at(2026, 9, 7, 23, 0).toISOString(), duration: 7200, data: {status: 'not-afk'}},
    ];
    eq(S.activeSecondsToday(events, now), 3600 + 3600);
});
test('activeSecondsToday clips an open event at now', () => {
    const now = at(2026, 9, 8, 12, 0);
    const events = [{timestamp: at(2026, 9, 8, 11, 0).toISOString(), duration: 99999, data: {status: 'not-afk'}}];
    eq(S.activeSecondsToday(events, now), 3600);
});
test('activeSecondsToday tolerates junk entries', () => {
    eq(S.activeSecondsToday([null, {}, {timestamp: 'bad', data: {status: 'not-afk'}}], at(2026, 9, 8)), 0);
});

print(`${passed} passed, ${failures.length} failed`);
for (const f of failures)
    print('FAIL ' + f);
if (failures.length)
    throw new Error('tests failed');
