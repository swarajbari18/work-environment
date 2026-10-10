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


class Staged(unittest.TestCase):
    """A vault installed into a temporary root (no root, no systemd)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "root")
        bank = os.path.join(self.tmp.name, "bank")
        os.makedirs(bank)
        questions.write_jsonl(os.path.join(bank, "a.jsonl"), list(bank_of(400).values()))
        r = run([os.path.join(HERE, "install.sh"), "--root", self.root, "--owner-uid", str(UID), "--bank", bank,
                 "--no-systemctl"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.managed = os.path.join(self.root, "etc", "claude-code", "managed-settings.json")

    def tearDown(self):
        self.tmp.cleanup()

    def fence(self, *extra):
        return run([os.path.join(HERE, "install-agent-fence.sh"), "--root", self.root, *extra])

    def load(self, path):
        with open(path) as fh:
            return json.load(fh)


class InstallerGainsP3(Staged):
    def test_new_runtime_files_and_directories(self):
        code = os.path.join(self.root, "opt", "wenv-vault")
        for f in ("settings.py", "claude_guard.py", "fence.py"):
            self.assertTrue(os.path.isfile(os.path.join(code, f)), f)
        for d in ("profile", "db"):
            self.assertTrue(os.path.isdir(os.path.join(self.root, "etc", "dconf", d)), d)

    def test_unit_lets_the_daemon_write_dconf(self):
        with open(os.path.join(self.root, "etc", "systemd", "system", "wenv-vaultd.service")) as fh:
            unit = fh.read()
        self.assertIn("ReadWritePaths=/var/lib/wenv-vault /etc/wenv-vault /etc/dconf", unit)

    def test_the_installed_guard_runs_standalone(self):
        r = run(["python3", "-I", os.path.join(self.root, "opt", "wenv-vault", "claude_guard.py")],
                input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "sudo ls"}}))
        self.assertEqual(r.returncode, 2)
        self.assertIn("Blocked by the vault", r.stderr)


class AgentFence(Staged):
    def test_fresh_install(self):
        r = self.fence()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(stat.S_IMODE(os.stat(self.managed).st_mode), 0o644)
        data = self.load(self.managed)
        self.assertIn("Edit(//etc/wenv-vault/**)", data["permissions"]["deny"])
        self.assertIn("Bash(sudo *)", data["permissions"]["deny"])
        self.assertEqual(data["hooks"]["PreToolUse"][0]["hooks"][0]["args"], ["-I", "/opt/wenv-vault/claude_guard.py"])
        self.assertIn("MultiEdit(//etc/wenv-vault/**)", data["permissions"]["deny"])
        self.assertNotIn("allowManagedHooksOnly", json.dumps(data))
        self.assertIn("New Claude Code sessions", r.stdout)

    def test_merges_into_existing_and_keeps_a_backup(self):
        os.makedirs(os.path.dirname(self.managed))
        original = {"permissions": {"deny": ["Bash(curl *)"], "disableBypassPermissionsMode": "disable"},
                    "env": {"X": "1"}}
        with open(self.managed, "w") as fh:
            json.dump(original, fh)
        r = self.fence()
        self.assertEqual(r.returncode, 0, r.stderr)
        data = self.load(self.managed)
        self.assertIn("Bash(curl *)", data["permissions"]["deny"])
        self.assertEqual(data["permissions"]["disableBypassPermissionsMode"], "disable")
        self.assertEqual(data["env"], {"X": "1"})
        backups = [n for n in os.listdir(os.path.dirname(self.managed)) if ".bak-" in n]
        self.assertEqual(len(backups), 1)
        self.assertEqual(self.load(os.path.join(os.path.dirname(self.managed), backups[0])), original)

    def test_rerun_changes_nothing(self):
        self.fence()
        first = self.load(self.managed)
        self.assertEqual(self.fence().returncode, 0)
        self.assertEqual(self.load(self.managed), first)

    def test_invalid_existing_file_is_never_overwritten(self):
        os.makedirs(os.path.dirname(self.managed))
        with open(self.managed, "w") as fh:
            fh.write("{ not json")
        r = self.fence()
        self.assertNotEqual(r.returncode, 0)
        with open(self.managed) as fh:
            self.assertEqual(fh.read(), "{ not json")

    def test_remove_gives_back_exactly_what_was_there(self):
        os.makedirs(os.path.dirname(self.managed))
        original = {"permissions": {"deny": ["Bash(curl *)"]}}
        with open(self.managed, "w") as fh:
            json.dump(original, fh)
        self.fence()
        self.assertEqual(self.fence("--remove").returncode, 0)
        self.assertEqual(self.load(self.managed), original)

    def test_remove_deletes_a_file_that_was_only_ours(self):
        self.fence()
        self.assertEqual(self.fence("--remove").returncode, 0)
        self.assertFalse(os.path.exists(self.managed))

    def test_needs_the_vault_installed(self):
        r = run([os.path.join(HERE, "install-agent-fence.sh"), "--root", os.path.join(self.tmp.name, "empty")])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not installed", r.stderr)


class DockerOff(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = os.path.join(self.tmp.name, "calls")
        self.fake = os.path.join(self.tmp.name, "gpasswd")
        with open(self.fake, "w") as fh:
            fh.write(f'#!/bin/sh\necho "$@" >> "{self.log}"\n')
        os.chmod(self.fake, 0o755)

    def tearDown(self):
        self.tmp.cleanup()

    def run_script(self, *args, groups="alice sudo docker", stdin="", gpasswd=True):
        env = {**os.environ, "WENV_FAKE_GROUPS": groups}
        if gpasswd:
            env["WENV_GPASSWD"] = self.fake
        return run([os.path.join(HERE, "docker-off.sh"), "--user", "alice", *args], env=env, input=stdin)

    def calls(self):
        try:
            with open(self.log) as fh:
                return fh.read().split("\n")[:-1]
        except FileNotFoundError:
            return []

    def test_yes_removes_the_group_and_says_what_changes(self):
        r = self.run_script("--yes")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls(), ["-d alice docker"])
        for phrase in ("sudo docker", "bind mounts", "images, volumes and containers", "usermod -aG docker alice",
                       "Log out"):
            self.assertIn(phrase, r.stdout)

    def test_typed_yes_is_enough_and_anything_else_cancels(self):
        self.assertEqual(self.run_script(stdin="yes\n").returncode, 0)
        self.assertEqual(self.calls(), ["-d alice docker"])
        self.assertNotEqual(self.run_script(stdin="y\n").returncode, 0)
        self.assertNotEqual(self.run_script(stdin="").returncode, 0)
        self.assertEqual(self.calls(), ["-d alice docker"])                    # still just the one call

    def test_dry_run_changes_nothing(self):
        r = self.run_script("--dry-run", gpasswd=False)
        self.assertEqual(r.returncode, 0)
        self.assertIn("dry run", r.stdout)
        self.assertEqual(self.calls(), [])

    def test_already_out_of_the_group(self):
        r = self.run_script("--yes", groups="alice sudo")
        self.assertEqual(r.returncode, 0)
        self.assertIn("nothing to do", r.stdout)
        self.assertEqual(self.calls(), [])

    def test_refuses_root_and_a_missing_user(self):
        env = {**os.environ, "WENV_FAKE_GROUPS": "root docker", "WENV_GPASSWD": self.fake}
        r = run([os.path.join(HERE, "docker-off.sh"), "--user", "root", "--yes"], env=env)
        self.assertNotEqual(r.returncode, 0)
        env.pop("SUDO_USER", None)
        r = run([os.path.join(HERE, "docker-off.sh"), "--yes"], env={k: v for k, v in env.items() if k != "SUDO_USER"})
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.calls(), [])


class UninstallCleansUp(Staged):
    def test_locks_profile_and_fence_go_with_the_daemon(self):
        db = os.path.join(self.root, "etc", "dconf", "db")
        os.makedirs(os.path.join(db, "wenv.d", "locks"))
        with open(os.path.join(db, "wenv"), "wb") as fh:
            fh.write(b"compiled")
        with open(os.path.join(self.root, "etc", "dconf", "profile", "user"), "w") as fh:
            fh.write("user-db:user\nsystem-db:wenv\n")
        os.makedirs(os.path.dirname(self.managed))
        with open(self.managed, "w") as fh:
            json.dump({"permissions": {"deny": ["Bash(curl *)"]}}, fh)
        self.assertEqual(self.fence().returncode, 0)

        token_dir = os.path.join(self.root, "run", "wenv-vault")
        os.makedirs(token_dir, exist_ok=True)
        import datetime as dt
        with open(os.path.join(token_dir, "uninstall-ok"), "w") as fh:
            json.dump({"expires": (dt.datetime.now() + dt.timedelta(minutes=5)).isoformat(timespec="seconds")}, fh)
        r = run([os.path.join(self.root, "opt", "wenv-vault", "uninstall.sh")],
                env={**os.environ, "WENV_VAULT_ROOT": self.root})
        self.assertEqual(r.returncode, 0, r.stderr)

        self.assertFalse(os.path.exists(os.path.join(db, "wenv.d")))
        self.assertFalse(os.path.exists(os.path.join(db, "wenv")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "etc", "dconf", "profile", "user")))
        self.assertEqual(self.load(self.managed), {"permissions": {"deny": ["Bash(curl *)"]}})
        self.assertFalse(os.path.exists(os.path.join(self.root, "opt", "wenv-vault")))

    def test_a_profile_with_other_lines_keeps_them(self):
        profile = os.path.join(self.root, "etc", "dconf", "profile", "user")
        with open(profile, "w") as fh:
            fh.write("user-db:user\nsystem-db:site\nsystem-db:wenv\n")
        token_dir = os.path.join(self.root, "run", "wenv-vault")
        os.makedirs(token_dir, exist_ok=True)
        import datetime as dt
        with open(os.path.join(token_dir, "uninstall-ok"), "w") as fh:
            json.dump({"expires": (dt.datetime.now() + dt.timedelta(minutes=5)).isoformat(timespec="seconds")}, fh)
        r = run([os.path.join(self.root, "opt", "wenv-vault", "uninstall.sh")],
                env={**os.environ, "WENV_VAULT_ROOT": self.root})
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(profile) as fh:
            self.assertEqual(fh.read(), "user-db:user\nsystem-db:site\n")


if __name__ == "__main__":
    unittest.main()
