// Run: gjs -m test_scanout.js
import {ScanoutGuard} from './scanout.js';

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

const ACTIVE = 1;
const UUID = 'colorblind-filters@G-dH.github.com';

// A stand-in for the shell: an extension manager whose filter state the test can change and announce.
function world(initialState) {
    const calls = [];
    const listeners = new Map();
    let nextId = 1;
    const state = {filter: initialState};
    const extensionManager = {
        connect(signal, fn) {
            const id = nextId++;
            listeners.set(id, fn);
            calls.push(`connect:${signal}`);
            return id;
        },
        disconnect(id) {
            listeners.delete(id);
            calls.push('disconnect');
        },
        lookup(uuid) {
            return uuid === UUID && state.filter !== null ? {state: state.filter} : null;
        },
    };
    const guard = new ScanoutGuard({
        extensionManager, filterUuid: UUID, activeState: ACTIVE,
        inhibit: () => calls.push('inhibit'),
        release: () => calls.push('release'),
    });
    return {
        guard, calls,
        set(next) {
            state.filter = next;
            for (const fn of [...listeners.values()])
                fn();
        },
    };
}
const only = (calls, name) => calls.filter(c => c === name).length;

test('filter already active at start: direct scanout is inhibited once', () => {
    const w = world(ACTIVE);
    w.guard.enable();
    eq(only(w.calls, 'inhibit'), 1);
});

test('filter not active at start: nothing is inhibited', () => {
    const w = world(0);
    w.guard.enable();
    eq(only(w.calls, 'inhibit'), 0);
});

test('turning the filter on later inhibits, turning it off releases', () => {
    const w = world(0);
    w.guard.enable();
    w.set(ACTIVE);
    eq(only(w.calls, 'inhibit'), 1);
    w.set(0);
    eq(only(w.calls, 'release'), 1);
});

test('repeated active notifications do not stack inhibits', () => {
    const w = world(ACTIVE);
    w.guard.enable();
    w.set(ACTIVE);
    w.set(ACTIVE);
    eq(only(w.calls, 'inhibit'), 1);
});

test('disable releases a held inhibit exactly once and stops listening', () => {
    const w = world(ACTIVE);
    w.guard.enable();
    w.guard.disable();
    w.guard.disable();
    eq(only(w.calls, 'release'), 1);
    eq(only(w.calls, 'disconnect'), 1);
});

test('disable without ever inhibiting releases nothing', () => {
    const w = world(0);
    w.guard.enable();
    w.guard.disable();
    eq(only(w.calls, 'release'), 0);
});

test('the filter extension missing entirely counts as not active', () => {
    const w = world(null);
    w.guard.enable();
    eq(only(w.calls, 'inhibit'), 0);
});

test('an inhibit that throws is tolerated and retried on the next change', () => {
    let fail = true;
    const calls = [];
    const listeners = [];
    const guard = new ScanoutGuard({
        extensionManager: {connect: (_s, fn) => (listeners.push(fn), 1), disconnect() {}, lookup: () => ({state: ACTIVE})},
        filterUuid: UUID, activeState: ACTIVE,
        inhibit: () => { calls.push('try'); if (fail) throw new Error('boom'); },
        release: () => calls.push('release'),
    });
    guard.enable();
    fail = false;
    listeners[0]();
    eq(calls, ['try', 'try']);
});

print(`${passed} passed, ${failures.length} failed`);
for (const f of failures)
    print(`  FAIL ${f}`);
if (failures.length > 0)
    imports.system.exit(1);
