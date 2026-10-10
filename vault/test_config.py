import json
import os
import stat
import tempfile
import unittest

import config
import matcher


class Apps(unittest.TestCase):
    def test_default_apps_equal_the_matcher_rules(self):
        for default, matcher_app in zip(config.DEFAULT_APPS, matcher.DEFAULT_APPS):
            for key in ("name", "comm", "exe_prefix", "env_any"):
                self.assertEqual(default[key], matcher_app[key])

    def test_accepts_a_normal_rule_and_normalizes(self):
        app = config.normalize_app({"name": "discord", "comm": [" Discord "], "launch": ["/usr/bin/discord"]})
        self.assertEqual(app, {"name": "discord", "comm": ["Discord"], "exe_prefix": [], "env_any": [],
                               "launch": ["/usr/bin/discord"]})

    def test_rejects_malformed(self):
        bad = [
            "x", {}, {"name": "Bad Name", "comm": ["x"]}, {"name": "ok"}, {"name": "ok", "comm": "steam"},
            {"name": "ok", "comm": [""]}, {"name": "ok", "comm": ["a" * 16]}, {"name": "ok", "comm": ["a"] * 17},
            {"name": "ok", "comm": [1]}, {"name": "ok", "env_any": ["has space"]},
        ]
        for raw in bad:
            with self.assertRaises(ValueError, msg=raw):
                config.normalize_app(raw)

    def test_protected_processes_cannot_be_added(self):
        for comm in ("bash", "gnome-shell", "claude", "systemd", "python3", "Xwayland", "sudo", "wenv-vaultd"):
            with self.assertRaises(ValueError, msg=comm):
                config.normalize_app({"name": "x", "comm": [comm]})

    def test_broad_or_protected_paths_are_refused(self):
        for prefix in ("/", "/usr", "/usr/bin/", "/usr/lib", "/opt", "/snap", "/home", "relative/path",
                       "/usr/lib/systemd/", "/usr/lib/systemd/user", "/opt/wenv-vault/", "/usr/sbin/cron", "/etc"):
            with self.assertRaises(ValueError, msg=prefix):
                config.normalize_app({"name": "x", "exe_prefix": [prefix]})

    def test_specific_paths_are_fine(self):
        for prefix in ("/snap/firefox/", "/opt/google/chrome", "/home/u/games/", "/usr/games/steam"):
            self.assertEqual(config.normalize_app({"name": "x", "exe_prefix": [prefix]})["exe_prefix"], [prefix])

    def test_ubiquitous_env_markers_are_refused(self):
        with self.assertRaises(ValueError):
            config.normalize_app({"name": "x", "env_any": ["HOME"]})
        self.assertEqual(config.normalize_app({"name": "x", "env_any": ["SteamAppId"]})["env_any"], ["SteamAppId"])


class PolicyFile(unittest.TestCase):
    def test_default_policy_is_valid_and_dry_run(self):
        p = config.default_policy(1000)
        self.assertIs(p["enforce"], False)
        self.assertEqual([a["name"] for a in p["apps"]], ["steam", "firefox"])
        self.assertEqual(config.validate_policy(p), p)

    def test_round_trip_and_mode(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "etc", "policy.json")
            config.save_policy(path, config.default_policy(1000))
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o644)
            policy, err = config.load_policy(path)
            self.assertIsNone(err)
            self.assertEqual(policy, config.default_policy(1000))
            self.assertEqual([n for n in os.listdir(os.path.dirname(path)) if n.endswith(".tmp")], [])

    def test_missing_corrupt_and_invalid_all_fail_open(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "policy.json")
            self.assertIsNone(config.load_policy(path)[0])
            with open(path, "w") as fh:
                fh.write("{not json")
            policy, err = config.load_policy(path)
            self.assertIsNone(policy)
            self.assertIn("unusable", err)
            bad = config.default_policy(1000)
            bad["apps"].append({"name": "sh", "comm": ["bash"]})
            with open(path, "w") as fh:
                json.dump(bad, fh)
            policy, err = config.load_policy(path)
            self.assertIsNone(policy)
            self.assertIn("protected", err)

    def test_duplicates_and_types(self):
        p = config.default_policy(1000)
        p["apps"].append(dict(p["apps"][0]))
        with self.assertRaises(ValueError):
            config.validate_policy(p)
        for patch in ({"enforce": "yes"}, {"owner_uid": -1}, {"owner_uid": True}, {"version": 2}, {"apps": {}}):
            with self.assertRaises(ValueError, msg=patch):
                config.validate_policy({**config.default_policy(1000), **patch})

    def test_save_refuses_invalid_and_leaves_old_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "policy.json")
            config.save_policy(path, config.default_policy(1000))
            with open(path) as fh:
                before = fh.read()
            with self.assertRaises(ValueError):
                config.save_policy(path, {**config.default_policy(1000), "enforce": 3})
            with open(path) as fh:
                self.assertEqual(fh.read(), before)


class PathsLayout(unittest.TestCase):
    def test_root_prefix(self):
        p = config.Paths.from_root("/tmp/stage/")
        self.assertEqual(p.policy_file, "/tmp/stage/etc/wenv-vault/policy.json")
        self.assertEqual(p.bank_dir, "/tmp/stage/var/lib/wenv-vault/bank")
        self.assertEqual(p.uninstall_token, "/tmp/stage/run/wenv-vault/uninstall-ok")

    def test_production_defaults(self):
        p = config.Paths.from_root("")
        self.assertEqual(p.state_file, "/var/lib/wenv-vault/state.json")
        self.assertEqual(p.socket, "/run/wenv-vault/vaultd.sock")


if __name__ == "__main__":
    unittest.main()
