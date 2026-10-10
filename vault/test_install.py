import datetime as dt
import json
import os
import stat
import subprocess
import tempfile
import unittest

import config
import questions
from test_engine import bank_of

HERE = os.path.dirname(os.path.abspath(__file__))
UID = os.getuid()


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120, **kw)


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


class RootInstaller(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "root")
        self.bank = os.path.join(self.tmp.name, "mybank")
        os.makedirs(self.bank)
        questions.write_jsonl(os.path.join(self.bank, "a.jsonl"), list(bank_of(400).values()))

    def tearDown(self):
        self.tmp.cleanup()

    def install(self, *extra, bank=None, uid=UID):
        return run([os.path.join(HERE, "install.sh"), "--root", self.root, "--owner-uid", str(uid),
                    "--bank", bank or self.bank, "--no-systemctl", *extra])

    def p(self, *parts):
        return os.path.join(self.root, *parts)

    def test_fresh_install_layout_and_modes(self):
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        code = self.p("opt", "wenv-vault")
        for f in ("vaultd.py", "protocol.py", "engine.py", "client.py", "gate.py", "cli.py", "agent.py", "launch.py"):
            self.assertTrue(os.path.isfile(os.path.join(code, f)), f)
        self.assertEqual(mode(os.path.join(code, "vaultd.py")), 0o644)
        self.assertEqual(mode(os.path.join(code, "uninstall.sh")), 0o755)
        for not_shipped in ("generators.py", "build_bank.py", "import_dataset.py", "watch.py", "test_engine.py"):
            self.assertFalse(os.path.exists(os.path.join(code, not_shipped)), not_shipped)
        self.assertEqual(mode(self.p("var", "lib", "wenv-vault")), 0o700)
        self.assertEqual(mode(self.p("var", "lib", "wenv-vault", "bank")), 0o700)
        self.assertEqual(mode(self.p("var", "lib", "wenv-vault", "bank", "a.jsonl")), 0o600)
        policy, err = config.load_policy(self.p("etc", "wenv-vault", "policy.json"))
        self.assertIsNone(err)
        self.assertEqual((policy["enforce"], policy["owner_uid"]), (False, UID))
        self.assertEqual(mode(self.p("etc", "wenv-vault", "policy.json")), 0o644)

    def test_unit_file(self):
        self.assertEqual(self.install().returncode, 0)
        with open(self.p("etc", "systemd", "system", "wenv-vaultd.service")) as fh:
            unit = fh.read()
        self.assertIn(f"--owner-uid {UID}", unit)
        self.assertIn("ExecStart=/usr/bin/python3 -I", unit)
        self.assertIn("Restart=always", unit)
        self.assertIn("RuntimeDirectory=wenv-vault", unit)
        self.assertIn("CapabilityBoundingSet=CAP_KILL CAP_SYS_PTRACE CAP_DAC_READ_SEARCH", unit)
        self.assertIn("ProtectSystem=strict", unit)
        self.assertNotIn("User=", unit)                           # it must run as root to read /proc of others

    def test_bank_is_moved_unless_kept(self):
        self.assertEqual(self.install().returncode, 0)
        self.assertEqual([n for n in os.listdir(self.bank) if n.endswith(".jsonl")], [])
        questions.write_jsonl(os.path.join(self.bank, "a.jsonl"), list(bank_of(400).values()))
        self.assertEqual(self.install("--keep-bank").returncode, 0)
        self.assertTrue(os.path.exists(os.path.join(self.bank, "a.jsonl")))

    def test_rerun_keeps_policy_and_state(self):
        self.assertEqual(self.install().returncode, 0)
        path = self.p("etc", "wenv-vault", "policy.json")
        policy, _ = config.load_policy(path)
        policy["enforce"] = True
        policy["apps"].append(config.normalize_app({"name": "discord", "comm": ["Discord"]}))
        config.save_policy(path, policy)
        with open(self.p("var", "lib", "wenv-vault", "state.json"), "w") as fh:
            json.dump({"seq": 7, "used": {"x": 7}}, fh)
        questions.write_jsonl(os.path.join(self.bank, "b.jsonl"), list(bank_of(400, seed=3).values()))
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        again, _ = config.load_policy(path)
        self.assertTrue(again["enforce"])
        self.assertEqual([a["name"] for a in again["apps"]], ["steam", "firefox", "discord"])
        with open(self.p("var", "lib", "wenv-vault", "state.json")) as fh:
            self.assertEqual(json.load(fh)["seq"], 7)

    def test_a_reinstall_reuses_the_installed_bank(self):
        self.assertEqual(self.install().returncode, 0)                       # moves the bank away from self.bank
        self.assertEqual([n for n in os.listdir(self.bank) if n.endswith(".jsonl")], [])
        r = run([os.path.join(HERE, "install.sh"), "--root", self.root, "--owner-uid", str(UID), "--no-systemctl"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("keeping the installed one", r.stdout)
        self.assertTrue(os.path.exists(self.p("var", "lib", "wenv-vault", "bank", "a.jsonl")))

    def test_an_explicit_empty_bank_is_never_silently_replaced_by_the_installed_one(self):
        self.assertEqual(self.install().returncode, 0)
        empty = os.path.join(self.tmp.name, "empty2")
        os.makedirs(empty)
        r = self.install(bank=empty)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no .jsonl", r.stderr)

    def test_refusals(self):
        self.assertNotEqual(self.install(uid=0).returncode, 0)
        r = self.install(bank=os.path.join(self.tmp.name, "nope"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no question bank", r.stderr)
        empty = os.path.join(self.tmp.name, "empty")
        os.makedirs(empty)
        self.assertNotEqual(self.install(bank=empty).returncode, 0)
        tiny = os.path.join(self.tmp.name, "tiny")
        os.makedirs(tiny)
        questions.write_jsonl(os.path.join(tiny, "t.jsonl"), list(bank_of(40).values())[:10])
        r = self.install(bank=tiny)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("usable questions", r.stderr)

    def test_a_broken_existing_policy_stops_the_install(self):
        self.assertEqual(self.install().returncode, 0)
        with open(self.p("etc", "wenv-vault", "policy.json"), "w") as fh:
            fh.write("{broken")
        questions.write_jsonl(os.path.join(self.bank, "a.jsonl"), list(bank_of(400).values()))
        r = self.install()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("self-test failed", r.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.bank, "a.jsonl")))      # nothing destroyed


class RootUninstaller(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "root")
        bank = os.path.join(self.tmp.name, "bank")
        os.makedirs(bank)
        questions.write_jsonl(os.path.join(bank, "a.jsonl"), list(bank_of(400).values()))
        r = run([os.path.join(HERE, "install.sh"), "--root", self.root, "--owner-uid", str(UID), "--bank", bank,
                 "--no-systemctl"])
        self.assertEqual(r.returncode, 0, r.stderr)

    def tearDown(self):
        self.tmp.cleanup()

    def uninstall(self, *extra):
        env = {**os.environ, "WENV_VAULT_ROOT": self.root}
        return run([os.path.join(self.root, "opt", "wenv-vault", "uninstall.sh"), *extra], env=env)

    def token(self, minutes):
        path = os.path.join(self.root, "run", "wenv-vault")
        os.makedirs(path, exist_ok=True)
        expires = dt.datetime.now() + dt.timedelta(minutes=minutes)
        with open(os.path.join(path, "uninstall-ok"), "w") as fh:
            json.dump({"expires": expires.isoformat(timespec="seconds")}, fh)

    def test_refuses_without_the_quiz(self):
        r = self.uninstall()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("wenv-vault uninstall", r.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.root, "opt", "wenv-vault", "vaultd.py")))

    def test_refuses_an_expired_approval(self):
        self.token(-5)
        self.assertNotEqual(self.uninstall().returncode, 0)
        self.assertTrue(os.path.exists(os.path.join(self.root, "opt", "wenv-vault")))

    def test_removes_code_keeps_state_by_default(self):
        self.token(5)
        r = self.uninstall()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.root, "opt", "wenv-vault")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "run", "wenv-vault", "uninstall-ok")))   # one use
        self.assertTrue(os.path.exists(os.path.join(self.root, "etc", "wenv-vault", "policy.json")))
        self.assertTrue(os.path.isdir(os.path.join(self.root, "var", "lib", "wenv-vault", "bank")))

    def test_purge_removes_everything(self):
        self.token(5)
        self.assertEqual(self.uninstall("--purge").returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.root, "etc", "wenv-vault")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "var", "lib", "wenv-vault")))


STEAM = "[Desktop Entry]\nName=Steam\nExec=/usr/games/steam %U\nType=Application\n"
FIREFOX = ("[Desktop Entry]\nName=Firefox\nExec=/snap/bin/firefox %u\nType=Application\n"
           "[Desktop Action new-private-window]\nName=New Private Window\nExec=/snap/bin/firefox --private-window %u\n")
FORZA = "[Desktop Entry]\nName=Forza Horizon 5\nExec=steam steam://rungameid/1551360\nType=Application\n"


class UserInstaller(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.home = os.path.join(t, "home")
        self.apps = os.path.join(t, "data", "applications")
        self.sysapps = os.path.join(t, "sysapps")
        for d in (self.home, self.apps, self.sysapps):
            os.makedirs(d)
        with open(os.path.join(self.apps, "steam.desktop"), "w") as fh:
            fh.write(STEAM)
        with open(os.path.join(self.apps, "Forza Horizon 5.desktop"), "w") as fh:
            fh.write(FORZA)
        with open(os.path.join(self.sysapps, "firefox_firefox.desktop"), "w") as fh:
            fh.write(FIREFOX)
        self.bin = os.path.join(t, "bin")
        self.env = {**os.environ, "HOME": self.home, "WENV_BIN_DIR": self.bin,
                    "XDG_CONFIG_HOME": os.path.join(t, "config"), "XDG_DATA_HOME": os.path.join(t, "data"),
                    "WENV_DESKTOP_DIRS": f"{self.apps}:{self.sysapps}",
                    "WENV_VAULT_SOCKET": os.path.join(t, "no-such.sock")}

    def tearDown(self):
        self.tmp.cleanup()

    def install(self, *extra):
        return run([os.path.join(HERE, "install-user.sh"), "--code", HERE, "--no-systemctl", *extra], env=self.env)

    def test_installs_wrappers_overrides_and_unit(self):
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for name in ("wenv-vault", "wenv-launch", "wenv-gate"):
            self.assertTrue(os.access(os.path.join(self.bin, name), os.X_OK), name)
        with open(os.path.join(self.apps, "steam.desktop")) as fh:
            steam = fh.read()
        self.assertIn(f"Exec={self.bin}/wenv-launch steam -- /usr/games/steam %U", steam)
        self.assertTrue(os.path.exists(os.path.join(self.apps, "steam.desktop.wenv-orig")))
        with open(os.path.join(self.apps, "Forza Horizon 5.desktop")) as fh:
            self.assertIn("wenv-launch steam -- steam steam://rungameid/1551360", fh.read())
        with open(os.path.join(self.apps, "firefox_firefox.desktop")) as fh:      # system entry shadowed in user dir
            ff = fh.read()
        self.assertIn("wenv-launch firefox -- /snap/bin/firefox --private-window %u", ff)
        with open(os.path.join(self.sysapps, "firefox_firefox.desktop")) as fh:
            self.assertEqual(fh.read(), FIREFOX)                                  # the system file is untouched
        with open(os.path.join(self.env["XDG_CONFIG_HOME"], "systemd", "user", "wenv-vault-agent.service")) as fh:
            unit = fh.read()
        self.assertIn(f"agent.py", unit)
        self.assertIn("WantedBy=graphical-session.target", unit)

    def test_wrappers_work_and_fail_open(self):
        self.assertEqual(self.install().returncode, 0)
        r = run([os.path.join(self.bin, "wenv-vault"), "status"], env=self.env)
        self.assertEqual(r.returncode, 1)
        self.assertIn("vault:", r.stderr)
        r = run([os.path.join(self.bin, "wenv-launch"), "x", "--", "/bin/echo", "started"], env=self.env)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "started"))        # no daemon: the app just starts

    def test_remove_restores_everything(self):
        self.assertEqual(self.install().returncode, 0)
        r = self.install("--remove")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(os.path.join(self.apps, "steam.desktop")) as fh:
            self.assertEqual(fh.read(), STEAM)
        with open(os.path.join(self.apps, "Forza Horizon 5.desktop")) as fh:
            self.assertEqual(fh.read(), FORZA)
        self.assertFalse(os.path.exists(os.path.join(self.apps, "firefox_firefox.desktop")))
        self.assertEqual(os.listdir(self.bin), [])

    def test_rerun_is_harmless(self):
        self.assertEqual(self.install().returncode, 0)
        with open(os.path.join(self.apps, "steam.desktop")) as fh:
            first = fh.read()
        self.assertEqual(self.install().returncode, 0)
        with open(os.path.join(self.apps, "steam.desktop")) as fh:
            self.assertEqual(fh.read(), first)
        with open(os.path.join(self.apps, "steam.desktop.wenv-orig")) as fh:
            self.assertEqual(fh.read(), STEAM)


if __name__ == "__main__":
    unittest.main()
