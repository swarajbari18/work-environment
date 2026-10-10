// Keeps the grayscale filter in force on Wayland.
//
// Colorblind Filters puts one desaturate shader on the shell's UI group. mutter 46
// (src/compositor/meta-compositor-view-native.c, find_scanout_candidate) hands the top window's buffer
// straight to the display whenever that window is opaque and its paint box equals the whole monitor,
// for example a maximized browser with the panel hidden. The shell's stage is then not painted at all
// (meta-stage-impl.c returns before meta_stage_impl_redraw_view_primary), so the shader never runs and
// that window shows in colour. Effects on the window actor are not consulted, so they cannot prevent it.
// The only switch is "unredirect inhibited": while it is set, no scanout candidate is ever chosen.
//
// This module holds that inhibit exactly while Colorblind Filters is ACTIVE, so `wenv-color off`
// restores direct scanout (full speed for games). It imports nothing; the shell objects are passed in,
// so the logic is tested with fakes in test_scanout.js.

export class ScanoutGuard {
    // env: {extensionManager, filterUuid, activeState, inhibit(), release()}
    constructor(env) {
        this._env = env;
        this._stateId = 0;
        this._inhibited = false;
    }

    enable() {
        this._stateId = this._env.extensionManager.connect('extension-state-changed', () => this._sync());
        this._sync();
    }

    disable() {
        if (this._stateId) {
            this._env.extensionManager.disconnect(this._stateId);
            this._stateId = 0;
        }
        this._release();
    }

    _filterActive() {
        const {extensionManager, filterUuid, activeState} = this._env;
        const extension = extensionManager.lookup(filterUuid);
        return !!extension && extension.state === activeState;
    }

    _sync() {
        const on = this._filterActive();
        if (on && !this._inhibited) {
            try {
                this._env.inhibit();
                this._inhibited = true;
            } catch (e) {
                console.error(`wenv-today: could not inhibit direct scanout: ${e}`);
            }
        } else if (!on) {
            this._release();
        }
    }

    _release() {
        if (!this._inhibited)
            return;
        this._inhibited = false;
        try {
            this._env.release();
        } catch (e) {
            console.error(`wenv-today: could not restore direct scanout: ${e}`);
        }
    }
}
