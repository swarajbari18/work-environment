import datetime as dt
import os
import shutil
import stat
import subprocess
import tempfile
import unittest

import settings
from settings import Entry, SettingsError

LIB = '''#!/usr/bin/env bash
STAGE_NATIVE=(
  "org.gnome.desktop.interface|gtk-theme|'ZorinGrey-Dark'"
  "org.gnome.desktop.interface|enable-animations|false"
  "org.gnome.desktop.search-providers|disabled|['org.gnome.Software.desktop', 'org.gnome.Weather.desktop']"
  "org.gnome.shell|favorite-apps|@as []"
)
STAGE_JUSTPERFECTION=(
  "org.gnome.shell.extensions.just-perfection|panel|false"
  "org.gnome.shell.extensions.just-perfection|animation|0"
)
STAGE_GRAYSCALE=(
  "org.gnome.shell.extensions.colorblind-filters|filter-name|'Desaturation'"
  "org.gnome.shell.extensions.colorblind-filters|filter-active|true"
)
all_keys() {
  printf '%s\\n' "${STAGE_NATIVE[@]}"
  printf '%s\\n' "org.gnome.shell|enabled-extensions|"
}
'''


def good():
    return [Entry("org.gnome.desktop.interface", "gtk-theme", "'ZorinGrey-Dark'"),
            Entry("org.gnome.desktop.interface", "enable-animations", "false"),
            Entry("org.gnome.shell", "favorite-apps", "@as []")]


class Parse(unittest.TestCase):
    def test_reads_every_stage_array_and_nothing_else(self):
        entries = settings.parse_lib_text(LIB)
        self.assertEqual(len(entries), 8)
        self.assertEqual(entries[0], Entry("org.gnome.desktop.interface", "gtk-theme", "'ZorinGrey-Dark'"))
        self.assertEqual(entries[2].value, "['org.gnome.Software.desktop', 'org.gnome.Weather.desktop']")
        self.assertEqual(entries[3].value, "@as []")
        self.assertNotIn(("org.gnome.shell", "enabled-extensions"), [(e.schema, e.key) for e in entries])

    def test_never_executes_the_file(self):
        evil = 'STAGE_X=(\n  "org.gnome.shell|favorite-apps|@as []"\n)\n$(touch /tmp/should-not-exist-wenv)\n'
        settings.parse_lib_text(evil)
        self.assertFalse(os.path.exists("/tmp/should-not-exist-wenv"))

    def test_the_real_lib_sh_parses_to_34_less_the_excluded(self):
        path = os.path.expanduser("~/work-environment/lib.sh")
        if not os.path.exists(path):
            self.skipTest("lib.sh not present")
        with open(path) as fh:
            parsed = settings.parse_lib_text(fh.read())
        self.assertEqual(len(parsed), 33)                     # 34 minus enabled-extensions, which is not in STAGE_*
        kept = settings.validate_entries(parsed)
        self.assertEqual(len(kept), 32)                       # and minus filter-active
        self.assertNotIn(("org.gnome.shell.extensions.colorblind-filters", "filter-active"),
                         [(e.schema, e.key) for e in kept])


class Validation(unittest.TestCase):
    def test_accepts_and_drops_the_excluded_keys(self):
        raw = settings.parse_lib_text(LIB)
        kept = settings.validate_entries(raw)
        self.assertEqual(len(kept), 7)
        self.assertNotIn("filter-active", [e.key for e in kept])

    def test_rejections(self):
        bad = [
            [("org.gnome.settings-daemon.plugins.power", "sleep-inactive-ac-type", "'nothing'")],   # not our namespace
            [("org.gnome.desktop.interface", "gtk-theme", "'unterminated")],
            [("org.gnome.desktop.interface", "gtk-theme", "not a variant")],
            [("org.gnome.desktop.interface", "Gtk Theme", "'x'")],
            [("org.gnome.desktop.interface", "gtk-theme", "'a'\n[evil]")],
            [("org.gnome.desktop.interface", "gtk-theme", "'a'"), ("org.gnome.desktop.interface", "gtk-theme", "'b'")],
            [("../etc", "x", "1")],
            [],
            "not a list",
            [("only", "two")],
            [("org.gnome.desktop.interface", "gtk-theme", "")],
        ]
        for raw in bad:
            with self.assertRaises(SettingsError, msg=raw):
                settings.validate_entries(raw)

    def test_too_many(self):
        raw = [("org.gnome.shell.extensions.x", f"key-{i}", "1") for i in range(81)]
        with self.assertRaises(SettingsError):
            settings.validate_entries(raw)

    def test_dict_entries_are_accepted(self):
        out = settings.validate_entries([{"schema": "org.gnome.desktop.interface", "key": "gtk-theme",
                                         "value": "'X'"}])
        self.assertEqual(out, [Entry("org.gnome.desktop.interface", "gtk-theme", "'X'")])


class Rendering(unittest.TestCase):
    def test_keyfile_and_locks(self):
        entries = settings.validate_entries(settings.parse_lib_text(LIB))
        keyfile = settings.render_keyfile(entries)
        self.assertIn("[org/gnome/desktop/interface]\nenable-animations=false\ngtk-theme='ZorinGrey-Dark'", keyfile)
        self.assertIn("[org/gnome/shell/extensions/just-perfection]\nanimation=0\npanel=false", keyfile)
        locks = settings.render_locks(entries).splitlines()
        self.assertIn("/org/gnome/desktop/interface/gtk-theme", locks)
        self.assertNotIn("/org/gnome/shell/extensions/colorblind-filters/filter-active", locks)
        self.assertEqual(len(locks), 7)

    @unittest.skipUnless(shutil.which("dconf"), "needs dconf")
    def test_dconf_really_compiles_it(self):
        entries = settings.validate_entries(settings.parse_lib_text(LIB))
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "wenv.d", "locks"))
            with open(os.path.join(d, "wenv.d", "00-wenv-vault"), "w") as fh:
                fh.write(settings.render_keyfile(entries))
            with open(os.path.join(d, "wenv.d", "locks", "wenv-vault"), "w") as fh:
                fh.write(settings.render_locks(entries))
            out = os.path.join(d, "wenv")
            r = subprocess.run(["dconf", "compile", out, os.path.join(d, "wenv.d")], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertGreater(os.path.getsize(out), 0)
            with open(out, "rb") as fh:
                blob = fh.read()
            self.assertIn(b"ZorinGrey-Dark", blob)                          # the value made it into the database
            self.assertIn(b"just-perfection", blob)


class StateMachine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dconf = os.path.join(self.tmp.name, "etc", "dconf")
        self.manifest = os.path.join(self.tmp.name, "etc", "wenv-vault", "settings.json")
        self.now = dt.datetime(2026, 10, 12, 10, 0)
        self.updates = []
        self.fail_update = False

        def update(path):
            if self.fail_update:
                raise SettingsError("dconf update failed: simulated")
            self.updates.append(path)

        self.g = settings.Guard(self.dconf, self.manifest, run_update=update, clock=lambda: self.now)

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, path):
        with open(path) as fh:
            return fh.read()

    def test_init_writes_profile_values_locks_and_manifest(self):
        self.assertEqual(self.g.status()["initialized"], False)
        self.assertEqual(self.g.init(good()), 3)
        self.assertEqual(self.read(self.g.profile), "user-db:user\nsystem-db:wenv\n")
        self.assertIn("gtk-theme='ZorinGrey-Dark'", self.read(self.g.values_file))
        self.assertIn("/org/gnome/shell/favorite-apps", self.read(self.g.locks_file))
        self.assertEqual(self.updates, [self.dconf])
        self.assertEqual(stat.S_IMODE(os.stat(self.manifest).st_mode), 0o644)
        self.assertEqual(self.g.status(), {"initialized": True, "locked": True, "keys": 3, "unlocked_until": None})

    def test_init_twice_is_refused(self):
        self.g.init(good())
        with self.assertRaises(SettingsError):
            self.g.init(good())

    def test_an_existing_profile_is_extended_not_replaced(self):
        os.makedirs(os.path.dirname(self.g.profile))
        with open(self.g.profile, "w") as fh:
            fh.write("user-db:user\nsystem-db:site\n")
        self.g.init(good())
        self.assertEqual(self.read(self.g.profile), "user-db:user\nsystem-db:site\nsystem-db:wenv\n")
        self.g.unlock()
        self.g.commit(good())
        self.assertEqual(self.read(self.g.profile).count("system-db:wenv"), 1)

    def test_changed_values_cannot_be_committed_while_locked(self):
        self.g.init(good())
        changed = [Entry("org.gnome.desktop.interface", "gtk-theme", "'Adwaita'")] + good()[1:]
        with self.assertRaises(SettingsError) as cm:
            self.g.commit(changed)
        self.assertIn("unlock", str(cm.exception))
        self.assertIn("ZorinGrey-Dark", self.read(self.g.values_file))
        self.assertEqual(self.g.commit(good()), 3)                          # repeating the same values is harmless

    def test_unlock_commit_cycle(self):
        self.g.init(good())
        until = self.g.unlock()
        self.assertEqual(until, "2026-10-12T10:20:00")
        self.assertFalse(os.path.exists(self.g.locks_file))                 # writable now
        self.assertIn("gtk-theme='ZorinGrey-Dark'", self.read(self.g.values_file))   # old values still the net
        self.assertEqual(self.g.status()["locked"], False)
        changed = [Entry("org.gnome.desktop.interface", "gtk-theme", "'Adwaita'")] + good()[1:]
        self.assertEqual(self.g.commit(changed), 3)
        self.assertIn("gtk-theme='Adwaita'", self.read(self.g.values_file))
        self.assertTrue(os.path.exists(self.g.locks_file))
        self.assertEqual(self.g.status()["locked"], True)

    def test_an_uncommitted_window_reverts_at_expiry(self):
        self.g.init(good())
        self.g.unlock()
        self.now += dt.timedelta(minutes=19)
        self.assertFalse(self.g.tick())
        self.assertFalse(os.path.exists(self.g.locks_file))
        self.now += dt.timedelta(minutes=2)
        self.assertTrue(self.g.tick())
        self.assertTrue(os.path.exists(self.g.locks_file))
        self.assertIn("gtk-theme='ZorinGrey-Dark'", self.read(self.g.values_file))
        self.assertFalse(self.g.tick())                                     # nothing more to do

    def test_unlocking_again_inside_a_window_does_not_extend_it(self):
        self.g.init(good())
        first = self.g.unlock()
        self.now += dt.timedelta(minutes=5)
        self.assertEqual(self.g.unlock(), first)

    def test_a_failed_compile_rolls_everything_back(self):
        self.g.init(good())
        self.g.unlock()
        before_values = self.read(self.g.values_file)
        self.fail_update = True
        changed = [Entry("org.gnome.desktop.interface", "gtk-theme", "'Adwaita'")] + good()[1:]
        with self.assertRaises(SettingsError):
            self.g.commit(changed)
        self.assertEqual(self.read(self.g.values_file), before_values)
        self.assertFalse(os.path.exists(self.g.locks_file))
        self.assertEqual(self.g.status()["locked"], False)                  # the manifest was not advanced either

    def test_a_failed_first_init_leaves_nothing_behind(self):
        self.fail_update = True
        with self.assertRaises(SettingsError):
            self.g.init(good())
        self.assertFalse(os.path.exists(self.g.profile))
        self.assertFalse(os.path.exists(self.g.values_file))
        self.assertFalse(os.path.exists(self.manifest))

    def test_corrupt_manifest_is_reported_not_guessed(self):
        os.makedirs(os.path.dirname(self.manifest))
        with open(self.manifest, "w") as fh:
            fh.write("{broken")
        with self.assertRaises(SettingsError):
            self.g.status()

    def test_unlock_before_init(self):
        with self.assertRaises(SettingsError):
            self.g.unlock()
        with self.assertRaises(SettingsError):
            self.g.commit(good())

    @unittest.skipUnless(shutil.which("dconf"), "needs dconf")
    def test_the_real_update_compiles_in_any_directory(self):
        g = settings.Guard(self.dconf, self.manifest, clock=lambda: self.now)       # default: the real dconf
        g.init(good())
        compiled = os.path.join(self.dconf, "db", "wenv")
        self.assertTrue(os.path.getsize(compiled) > 0)
        g.unlock()
        before = os.path.getmtime(compiled)
        self.assertTrue(os.path.exists(compiled))
        self.assertGreaterEqual(before, 0)

    def test_a_missing_source_directory_is_an_error(self):
        with self.assertRaises(SettingsError):
            settings.dconf_update(os.path.join(self.tmp.name, "nothing-here"))


if __name__ == "__main__":
    unittest.main()
