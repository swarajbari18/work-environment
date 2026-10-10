import io
import json
import unittest

import claude_guard as g
import fence


def bash(cmd):
    return g.check("Bash", {"command": cmd})


class BashDecisions(unittest.TestCase):
    def test_ordinary_development_is_untouched(self):
        for cmd in (
            "ls -la", "git status", "git commit -m 'fix the parser'", "python3 -m unittest discover -s vault",
            "npm run build && npm test", "docker compose up -d", "grep -rn wenv-vault ~/work-environment",
            "wenv-vault status", "wenv-vault apps", "wenv-vault log -n 20", "cat /etc/hostname",
            "wenv-vault settings status", "~/.local/bin/wenv-vault settings  status",
            "./vault/install.sh --help", "gsettings get org.gnome.desktop.interface gtk-theme",
            "gsettings set org.gnome.desktop.peripherals.touchpad tap-to-click true",
            "systemctl --user status wenv-aw-watchdog.timer", "pkill -f 'node server.js'",
            "echo summary > /tmp/notes.txt", "cat vault/DESIGN.md", "find . -name '*.py' | xargs wc -l",
            "ssh host 'uptime'", "python3 vault/watch.py report", "bash -c 'echo hi'", "make su-test",
        ):
            self.assertIsNone(bash(cmd), cmd)

    def test_privilege_escalation_is_refused(self):
        for cmd in ("sudo apt install x", "echo hi && sudo -n true", "ls; sudo ls", "(sudo ls)", "pkexec bash",
                    "doas ls", "su - root", "su root", "$(sudo id)", "echo `sudo id`", "x | sudo tee /etc/hosts"):
            self.assertIsNotNone(bash(cmd), cmd)

    def test_guard_paths_in_any_spelling(self):
        for cmd in (
            "cat /etc/wenv-vault/policy.json", "ls /opt/wenv-vault", "rm -rf /var/lib/wenv-vault/bank",
            "python3 -c \"open('/etc/wenv-vault/policy.json','w').write('{}')\"",
            "cp x //etc//wenv-vault//policy.json", "cat /etc/./wenv-vault/policy.json",
            "cat /etc/foo/../wenv-vault/policy.json", "ls /etc/dconf/db", "echo x > /etc/claude-code/managed-settings.json",
            "nc -U /run/wenv-vault/vaultd.sock", "sed -i s/a/b/ /etc/systemd/system/wenv-vaultd.service",
        ):
            self.assertIsNotNone(bash(cmd), cmd)

    def test_vault_commands(self):
        for cmd in ("wenv-vault add evil --comm x", "wenv-vault remove firefox", "wenv-vault enforce off",
                    "wenv-vault settings unlock", "wenv-vault settings lock", "wenv-vault settings init",
                    "wenv-vault uninstall", "wenv-vault unlock", "wenv-vault gate",
                    "~/.local/bin/wenv-vault enforce off", "python3 -I /opt/x/cli.py remove steam",
                    "wenv-vault -v remove steam"):
            self.assertIsNotNone(bash(cmd), cmd)

    def test_other_ways_to_break_the_guard(self):
        for cmd in ("chattr -i /etc/foo", "dconf write /org/gnome/desktop/interface/gtk-theme \"'x'\"",
                    "dconf update", "dconf reset -f /org/gnome/", "systemctl stop wenv-vaultd",
                    "systemctl --user disable wenv-vault-agent", "pkill -f vaultd.py", "killall wenv-vaultd",
                    "kill $(pgrep -f vaultd)",
                    "gsettings set org.gnome.desktop.interface color-scheme 'default'",
                    "gsettings set org.gnome.shell favorite-apps \"['steam.desktop']\"",
                    "gsettings reset org.gnome.desktop.background picture-uri"):
            self.assertIsNotNone(bash(cmd), cmd)

    def test_reasons_are_actionable(self):
        self.assertIn("owner", bash("sudo ls"))
        self.assertIn("wenv-vault status", bash("cat /etc/wenv-vault/policy.json"))


class FileToolDecisions(unittest.TestCase):
    def test_guard_files_are_refused(self):
        for tool, field in (("Edit", "file_path"), ("Write", "file_path"), ("NotebookEdit", "notebook_path"),
                            ("MultiEdit", "file_path")):
            for path in ("/etc/wenv-vault/policy.json", "/opt/wenv-vault/vaultd.py",
                         "/etc/claude-code/managed-settings.json", "/etc/dconf/profile/user",
                         "/var/lib/wenv-vault/state.json", "/etc/wenv-vault/../wenv-vault/x",
                         "//etc//wenv-vault/policy.json"):
                self.assertIsNotNone(g.check(tool, {field: path, "content": "x"}), (tool, path))

    def test_documentation_that_mentions_guard_paths_is_fine(self):
        self.assertIsNone(g.check("Write", {"file_path": "/home/u/work-environment/vault/DESIGN.md",
                                            "content": "The policy lives in /etc/wenv-vault/policy.json and sudo is used."}))
        self.assertIsNone(g.check("Edit", {"file_path": "/home/u/README.md", "old_string": "/etc/dconf",
                                           "new_string": "/etc/wenv-vault"}))

    def test_similar_looking_paths_are_not_guard_paths(self):
        for path in ("/etc/wenv-vault-notes.txt", "/home/u/etc/wenv-vault/x", "/etc/dconfig/x", "/optional/wenv-vault"):
            self.assertIsNone(g.check("Write", {"file_path": path}), path)

    def test_other_tools_and_odd_inputs(self):
        self.assertIsNone(g.check("Read", {"file_path": "/etc/wenv-vault/policy.json"}))      # reading is not blocked here
        self.assertIsNone(g.check("Bash", "not a dict"))
        self.assertIsNone(g.check("Edit", None))
        self.assertIsNone(g.check("Bash", {}))


class HookProtocol(unittest.TestCase):
    def run_hook(self, payload):
        out, err = io.StringIO(), io.StringIO()
        code = g.main(stdin=io.StringIO(payload if isinstance(payload, str) else json.dumps(payload)), stderr=err)
        return code, err.getvalue()

    def test_block_is_exit_2_with_a_reason(self):
        code, err = self.run_hook({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                                   "tool_input": {"command": "sudo rm -rf /"}})
        self.assertEqual(code, 2)
        self.assertTrue(err.startswith("Blocked by the vault:"))

    def test_no_objection_is_exit_0_and_silent(self):
        code, err = self.run_hook({"tool_name": "Bash", "tool_input": {"command": "ls"}})
        self.assertEqual((code, err), (0, ""))

    def test_garbage_input_fails_open(self):
        for payload in ("", "not json", "[]", "null"):
            code, _ = self.run_hook(payload)
            self.assertEqual(code, 0, payload)


class Fence(unittest.TestCase):
    def test_fresh_file(self):
        out = fence.merge(None)
        self.assertIn("Edit(//etc/wenv-vault/**)", out["permissions"]["deny"])
        self.assertIn("Write(//opt/wenv-vault/**)", out["permissions"]["deny"])
        self.assertIn("Bash(sudo *)", out["permissions"]["deny"])
        hook = out["hooks"]["PreToolUse"][0]
        self.assertEqual(hook["matcher"], "Bash|Edit|Write|NotebookEdit|MultiEdit")
        self.assertEqual(hook["hooks"][0]["args"], ["-I", "/opt/wenv-vault/claude_guard.py"])

    def test_it_never_silences_the_users_own_hooks_or_enables_the_sandbox(self):
        out = fence.merge(None)
        self.assertNotIn("allowManagedHooksOnly", json.dumps(out))
        self.assertNotIn("sandbox", out)

    def test_merges_without_losing_or_duplicating(self):
        existing = {"model": "x", "permissions": {"deny": ["Bash(curl *)"], "allow": ["Read"]},
                    "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "/x"}]}],
                              "Stop": [{"hooks": []}]}}
        once = fence.merge(existing)
        twice = fence.merge(once)
        self.assertEqual(once, twice)
        self.assertEqual(once["model"], "x")
        self.assertEqual(once["permissions"]["allow"], ["Read"])
        self.assertIn("Bash(curl *)", once["permissions"]["deny"])
        self.assertEqual(len(once["hooks"]["PreToolUse"]), 2)
        self.assertIn("Stop", once["hooks"])
        self.assertEqual(len(set(once["permissions"]["deny"])), len(once["permissions"]["deny"]))
        self.assertEqual(existing["permissions"]["deny"], ["Bash(curl *)"])           # input untouched

    def test_malformed_existing_content_is_refused_not_overwritten(self):
        for bad in ([], "x", {"permissions": []}, {"permissions": {"deny": "no"}}, {"hooks": []},
                    {"hooks": {"PreToolUse": {}}}):
            with self.assertRaises(ValueError, msg=bad):
                fence.merge(bad)


class FenceUnmerge(unittest.TestCase):
    def test_removes_only_our_rules(self):
        existing = {"model": "x", "permissions": {"deny": ["Bash(curl *)"]},
                    "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "/x"}]}],
                              "Stop": [{"hooks": []}]}}
        back, changed = fence.unmerge(fence.merge(existing))
        self.assertTrue(changed)
        self.assertEqual(back, existing)

    def test_nothing_of_ours_means_no_change(self):
        out, changed = fence.unmerge({"permissions": {"deny": ["Bash(curl *)"]}})
        self.assertFalse(changed)
        self.assertEqual(out, {"permissions": {"deny": ["Bash(curl *)"]}})

    def test_a_pure_vault_file_empties_out(self):
        back, changed = fence.unmerge(fence.merge(None))
        self.assertEqual((back, changed), ({}, True))

    def test_odd_input_is_returned_untouched(self):
        self.assertEqual(fence.unmerge([1]), ([1], False))


if __name__ == "__main__":
    unittest.main()
