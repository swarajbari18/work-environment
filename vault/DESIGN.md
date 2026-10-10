# Vault: technical reference

The plain-language description is in the project's top-level `README.md`. This file is the reference for people
changing the code: the rules as implemented, the file map, and the limits.

Aim: friction against impulse and against automated agents, **not** security against the owner, who keeps `sudo`.

## Rules as implemented

| Action | Cost |
|---|---|
| Launch a vault app on a weekday | 30 questions, 24 right (80%). One pass unlocks all vault apps for that calendar day |
| Add an app to the vault | free |
| Remove an app from the vault | 60 questions (48 to pass); one removal per pass, ticket valid 10 minutes |
| Turn enforcement off, change vault settings, unlock the environment's settings, uninstall | 120 questions (96 to pass) |
| Turn enforcement on, lock settings (`settings init` / `lock`) | free |
| Edit anything inside the project folder | free |
| Retry after a fail | the same set, options reshuffled, unlimited |
| After a pass | the set is marked used; later sets draw unused questions; the oldest come back only when none remain |

Details that the table hides:

- **"Until midnight" is 00:10.** Permission has a 10-minute grace (`policy.GRACE`): a pass from yesterday, or the
  weekend, still counts until 00:10. Enforcement acts at that *hard end*.
- **A warning** is emitted once, from 10 minutes before midnight (`policy.WARN_BEFORE`), only while enforcing. The
  agent shows it as a window because notifications are switched off in this environment.
- **After a failed attempt** only the score is returned; the review (answers, explanations) is returned only after a
  pass.
- **A passed practice quiz** marks its questions used in the real vault, because its answers were shown.
- **Fixed in code** (not configurable): tier sizes, pass rate, free days, grace, warning lead, ticket lifetime
  (10 min), settings window (20 min), kill grace (8 s), poll interval (2 s), per-rule limits (16 items, 300 chars).
- **Configurable** (in `/etc/wenv-vault/policy.json`, written only by the daemon): the apps list and `enforce`.

## Modes

| Mode | Behaviour |
|---|---|
| dry-run (install default) | logs `would_block`; kills nothing; no windows; no warnings |
| enforcing | SIGTERM, then SIGKILL after 8 s; emits `blocked`; the agent opens the quiz and relaunches after a pass |
| fail-open | policy missing or invalid, or empty bank: no kills; reason in `wenv-vault status` |

`install.sh` keeps an existing policy on re-run, so re-installing does not reset enforcement.

## App rules

`comm` (exact process name, 15 characters max), `exe_prefix` (executable path starts with), `env_any` (any of these
environment variable *names* is set). A match makes the process and its descendants members. Never members: the
protected list in `matcher.SAFE_COMMS` (shells, terminals, editors, `python3`, `node`, `claude`, the desktop shell,
systemd, D-Bus, ...), and a safe process also stops descent. `config.normalize_app` rejects rules that name those, broad
path prefixes (under 8 characters, `/usr`, `/opt`, `/home`, ...), systemd/gnome-shell paths, and ubiquitous
environment variables. Consequence: apps implemented as python, node or code cannot be vaulted.

Defaults: `steam` (`comm=steam`, `env_any=SteamAppId,SteamGameId`, which also catches Proton games without the client)
and `firefox` (`exe_prefix=/snap/firefox/,/usr/lib/firefox/`).

## File map

**Rules and quiz**

| File | Purpose |
|---|---|
| `policy.py` | free days, 80% rule, phases, day token, tier hierarchy, `session()` (state, soft/hard end, warning); injectable clock `WENV_VAULT_NOW` |
| `questions.py` | schema, validation, bank loading, quota selection that never recycles while unused questions exist |
| `engine.py` | attempts, grading, same-set retry, used tracking, day token and tickets; never exposes the answer key |
| `generators.py` | 18 parametric generators; `test_generators.py` re-solves every answer independently |
| `import_dataset.py`, `fetch_bank.sh`, `build_bank.py` | public datasets to bank JSONL, reproducible from scratch |

**Service, window and launchers**

| File | Purpose |
|---|---|
| `config.py` | paths, policy file, app-rule validation |
| `protocol.py` | request handlers: status, quiz, practice, add/remove app, enforce, settings, uninstall approval |
| `enforce.py` | enforcement loop; dry-run logging; ending warnings; fail-open; closes expired unlock windows |
| `vaultd.py`, `client.py` | socket server (peer uid checked), event bus, reload on SIGHUP, client library |
| `matcher.py`, `watch.py` | process matching (with an environment cache); the log-only watcher of the first stage |
| `gate.py` | quiz window (GTK 4) over a headless `GateModel`; a draft is saved so a crash loses nothing |
| `launch.py`, `desktop.py` | `wenv-launch` wrapper and `.desktop` rewriting, so the quiz appears before the app starts |
| `cli.py`, `agent.py` | the `wenv-vault` command; the session agent (quiz on block, warning before midnight) |
| `install.sh`, `uninstall.sh`, `install-user.sh`, `dev-demo.sh`, `install-watch.sh` | root install with self-test and rollback; user pieces; throwaway demo; the log-only watcher |

**Environment protection**

| File | Purpose |
|---|---|
| `settings.py` | the 32 GNOME keys locked through a root-owned dconf database; unlock window, commit, revert at expiry |
| `claude_guard.py`, `fence.py` | the Claude Code hook and the managed-settings merger |
| `install-agent-fence.sh`, `docker-off.sh` | `sudo` scripts for the fence and for leaving the docker group |

The scripts the settings lock cooperates with (`apply.sh`, `verify.sh`, `restore.sh`, `lib.sh`) are in the **project
root**, not in this folder.

About 340 Python tests (`python3 -m unittest discover -s vault`), including real SIGTERM/SIGKILL against live
processes, the real daemon over a real socket, the GTK window driven headlessly on Broadway, and the installers run
in a staging root.

## Operating it

| Command | Effect | Cost |
|---|---|---|
| `wenv-vault status` / `apps` / `log` | look | free |
| `wenv-vault unlock` | take today's quiz now | 30 questions |
| `wenv-vault gate [--tier T] [--practice]` | open the quiz window for a tier | as the tier |
| `wenv-vault add NAME --comm C ... [--desktop ID]` | put an app in the vault | free |
| `wenv-vault remove NAME` | take an app out | 60 questions |
| `wenv-vault enforce on` / `off` | start / stop killing locked apps | free / 120 |
| `wenv-vault settings init` / `lock` | lock the environment's settings / commit a change | free |
| `wenv-vault settings unlock` | open a 20-minute window to change them | 120 questions |
| `wenv-vault uninstall`, then `sudo /opt/wenv-vault/uninstall.sh [--purge]` | remove the service, the locks and the fence | 120 questions |

`install-user.sh` wraps the launchers of `steam`, `Forza Horizon 5` and `firefox_firefox` (entries that do not exist
are skipped) and installs the `wenv-vault`, `wenv-launch` and `wenv-gate` wrappers. Other apps: `add --desktop`.
When an entry is a symlink (Steam makes one into its own installer folder) the override *replaces* the link rather
than writing through it, and `remove` puts the link back.

## The settings lock

`settings init` snapshots the 32 lockable keys of `lib.sh` (only if the live desktop already matches them) into a
root-owned dconf database whose lock file stops the user database from overriding them. `settings unlock` removes
only the lock file and opens a 20-minute window; the old values stay in the database as a safety net. Inside the
window you edit `lib.sh`, run `./apply.sh all`, then `settings lock` to commit. If you do not, the daemon re-adds the
lock and the old values win again (even when fail-open). Two keys are left unlocked on purpose, `enabled-extensions`
and `filter-active`, because `wenv-color` works by (de)activating the grayscale extension.

The daemon accepts only the schema namespaces the environment uses, at most 80 keys, and values that parse as
GVariant text; the "live desktop matches `lib.sh`" check lives in the CLI.

## The agent fence

`/etc/claude-code/managed-settings.json` outranks user and project settings. It denies `Edit`, `Write`,
`NotebookEdit` and `MultiEdit` on the guard paths and `sudo`, `pkexec`, `su`, `chattr`, and registers
`claude_guard.py` as a `PreToolUse` hook on `Bash|Edit|Write|NotebookEdit|MultiEdit`. The hook exits 2 (refuse) for:

- any command that mentions a guard path in any spelling (`..`, `//`);
- `sudo`, `pkexec`, `doas`, `su`, `chattr`, `dconf write|reset|update|load`;
- `gsettings set|reset` on the six guarded schemas, `systemctl` aimed at the vault, `kill`/`pkill` aimed at it;
- the mutating `wenv-vault` subcommands: `add`, `remove`, `enforce`, `settings init|lock|unlock`, `uninstall`,
  `unlock`, `gate`.

Allowed: `wenv-vault status|apps|log|settings status`. No sandbox and no `allowManagedHooksOnly` (the owner's own
hooks keep running). It is advisory: the wall is root ownership, the locks and leaving the `docker` group. A Bash
command that merely *mentions* a guard path is refused too; file tools are checked by path only.

## The bank

18,817 questions: 6,776 generated, AQuA-RAT 3,000 (Apache-2.0), ARC 2,500 (CC-BY-SA-4.0), LogiQA 2.0 2,500
(CC-BY-NC-SA-4.0), MMLU 1,872 (MIT), RACE 1,169 (non-commercial research), SciQ 1,000 (CC-BY-NC-3.0). Personal use
only; not redistributed. Quotas per 100: numeracy 20, logical 25, verbal 15, high-school maths 10, high-school
science 10, emotional 10, data interpretation 10. **Emotional has no questions**, so its 10% is redistributed.
Passage groups are drawn together. Items that need a picture or table are dropped by a pattern. LogiQA is
machine-translated and sometimes garbled; AQuA-RAT is noisy even after the rationale-versus-key filter; ARC, MMLU,
LogiQA and RACE carry no explanations.

Before install the bank lives wherever `fetch_bank.sh` put it; `install.sh --bank DIR` copies it to the root-only
`/var/lib/wenv-vault/bank/` (and, unless `--keep-bank`, deletes the readable copy).

## Not built

- Emotional and situational questions, more generators, a "bad key" quarantine.
- Routing `wenv-color` through the vault, so `enabled-extensions` could be locked too.
- A real-desktop check that the quiz window takes focus over a running full-screen game.

## Known limits

- The owner has `sudo`, so a determined owner can remove the vault; that costs commands and time, not a quiz.
- The daemon's socket trusts any process running as the owner, so an agent could talk to it directly; the hook only
  inspects command text.
- Anything in the `input` group can synthesize keystrokes into the quiz window.
- Pasting questions into a chatbot cannot be prevented.
- Chrome is unguarded by design. Firefox is matched only under `/snap/firefox/` and `/usr/lib/firefox/`.
- Enforcement polls every 2 seconds; a process not matched by name, path or environment marker is not caught.
