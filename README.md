# A quiet computer

**An operating environment designed to make it easy to start working and slightly harder to wander off.**

This project turns a Linux laptop into a calm, work-only machine. It strips away the small things that tug at your
attention (colour, notifications, animations, icons, a busy desktop), it keeps an honest record of where your time
goes, and it can place the apps that swallow your evenings (a game launcher, a web browser) behind a short quiz on
weekdays.

It is a personal tool, built over a few days in October 2026 for one person's own laptop and used daily by its
author. It is not a polished product, and it is honest about what is proven and what is not (see
[Known limits](#known-limits-and-honest-caveats)).

- **Built and tested on:** Zorin OS 18.1 (GNOME 46, Wayland), 64-bit Intel/AMD.
- **Needs:** a normal user account with administrator (`sudo`) rights, and an internet connection to install.
- **Time to install:** about 15 minutes for the desktop, plus a few more for the vault.

---

## Contents

1. [Why this exists](#why-this-exists)
2. [What it does to your desktop](#what-it-does-to-your-desktop)
3. [The grayscale filter, in full](#the-grayscale-filter-in-full)
4. [The Today widget](#the-today-widget)
5. [Measuring where your time goes](#measuring-where-your-time-goes)
6. [Safety nets](#safety-nets)
7. [The Vault](#the-vault)
8. [Installing on a new laptop](#installing-on-a-new-laptop)
9. [Everyday commands](#everyday-commands)
10. [Known limits and honest caveats](#known-limits-and-honest-caveats)
11. [Taking it all back out](#taking-it-all-back-out)
12. [What is in this folder](#what-is-in-this-folder)
13. [Credits and licences](#credits-and-licences)

---

## Why this exists

Most computers are built to be interesting. Every colour, badge, sound and sliding animation is a small invitation to
look away from what you came to do. For someone who procrastinates, the pattern is familiar: the work becomes
difficult, the difficulty feels like "too much", a browser tab opens, and an hour disappears.

This project is a bet that you can change that pattern by changing the *surroundings* rather than relying on
willpower in the moment. It is built on a few simple ideas:

| Idea | What it means here |
|---|---|
| **Make it dull** | A black desktop, grey everything, nothing that begs to be clicked. |
| **Make it the same every day** | No novelty to discover; the machine looks identical each morning. |
| **Make notifications silent, not "less"** | Pop-ups are switched off entirely, not merely reduced. |
| **Add one deliberate step, not a wall** | The vault asks you to think for a minute; it does not forbid anything on weekends. |
| **Change the situation, not the person** | Rearrange what is in front of you instead of hoping to resist it. |
| **Measure, do not guess** | A quiet recorder shows where time really went. |

Two things it is **not**: a punishment, and a replacement for the decision to work. It is a nudge you set up in a
calm moment, to help the version of you that is tired or tempted later.

> The grayscale idea rests on a study of smartphone use, not of desktops. Treat it as a reasonable hypothesis that
> this project lets you measure, not as a proven cure.

---

## What it does to your desktop

Everything below is applied by one command and can be undone by one command (see
[Taking it all back out](#taking-it-all-back-out)). Nothing is changed before your original settings have been
saved.

### Colour and look
- **A black desktop.** The wallpaper picture is removed and replaced with solid black. The lock screen uses plain
  black too.
- **A grey, dark theme.** Windows, icons and the shell use Zorin's "Grey Dark" look, with the dark colour scheme.
- **Everything in grayscale.** A screen-wide filter removes colour from the whole display. This has its own section
  because it has several edge cases: [The grayscale filter, in full](#the-grayscale-filter-in-full).

### Silence
- **No pop-up banners.** Notifications do not appear on screen.
- **Nothing on the lock screen.** Notification previews are hidden there as well.

### A desktop with nothing to click
- **No icons on the desktop**, no pinned apps, and no wallpaper to look at.
- **The top bar is hidden** while you work. It appears only inside the Overview (press the Super key).
- **The dock is hidden.**
- **The application grid is not offered.** Its button is removed, its keyboard shortcut is cleared, and
  double-tapping Super does not open it. Pressing Super still opens the Overview, where you can find any app by
  typing its name.
- **Search stays focused on your own things.** The Software and Weather suggestions are removed from search.
- **You start on the desktop**, not in the Overview.
- **No pop-up when you change workspace.**

### Stillness
- **Animations are off**, both in the interface and in the shell.
- **Six decorative Zorin features are switched off:** the taskbar, desktop icons, rotating desktop cube,
  window-move wobble, "magic lamp" minimise effect and the spatial window switcher.

### What is deliberately left alone
Your files, your apps, your keyboard shortcuts for everyday window handling, and every other extension you had
enabled. The environment changes how things *look and announce themselves*; it does not take your tools away.

Altogether **34 settings** are changed and recorded (see [Safety nets](#safety-nets)).

---

## The grayscale filter, in full

Grayscale is the most visible change and the one with the most edge cases, so here is everything it does.

**What it is.** A screen-wide filter (a GNOME extension called *Colorblind Filters*) set to "desaturation" at 100%
strength. Every pixel the desktop draws loses its colour, in every application.

**What it covers.** All windows, all apps, the desktop, the Overview, and the Today widget. It is applied by the
desktop itself, so it does not matter which program is drawing.

**How you get colour back (for a game, a video, a design task).**

```
./wenv-color off      # colour on, and it stays on, even after you log out and back in
./wenv-color on       # grayscale back on
./wenv-color status   # tells you whether the filter is active
```

This is deliberately a command you have to type. There is no button.

**Why there is no button.** The extension normally adds a small eye icon to the top bar that switches the filter off
with one click. The installer removes that icon, so colour cannot come back by accident, or in a moment of
weakness, with a single click. The change takes effect after your next log-in, because GNOME Shell keeps the old code
in memory until then.

**Colour mode and re-applying.** Colour mode is "on until you say otherwise". Note that running `./apply.sh grayscale`
or `./apply.sh all` again re-enables the filter, which undoes a deliberate `wenv-color off`.

**Games and full speed.** On modern Linux (Wayland), the graphics driver can draw a window that fills the whole
screen *directly*, skipping the desktop's filter. With the top bar hidden, a maximised browser fills the screen and
could therefore have shown in colour. To stop that, the Today widget carries a small guard that tells the desktop
not to draw windows directly while the filter is on. When you switch to colour mode, the guard is released, so
games run at full speed. *The cause was confirmed by reading the display code; the cure is in place, but a
screenshot-level confirmation on every setup was not obtained.*

**Things that are not grayscale.**
- The **sign-in screen** (before you log in) is drawn by a separate system service, outside your session. It still
  shows Zorin's wallpaper. Several attempts to change this did not succeed, and it was left as it is.
- Anything the filter cannot see: it is applied to what the desktop displays. **Screenshots and screen
  recordings** were not tested.

**How it interacts with the rest.**
- The vault's *settings lock* intentionally leaves the grayscale switch writable, so `wenv-color` keeps working
  while everything else is locked.
- `./verify.sh` reports the grayscale checks as failing while colour mode is on. That is by design: it is telling
  you the environment is not in its working state.

---

## The Today widget

A small, quiet block drawn on the empty desktop background. It tells you three things and lets you keep a short list
of tasks.

**What it shows**
- **How much of today and this year is left**, as a percentage (for example "Day 62% left · Year 71% left").
- **The next open task**, or "Nothing open. Add a task."
- **A tally:** tasks left, tasks done today, and how long you have been actively at the computer today.

**How you use it**
- Click the top lines to expand or collapse it.
- Click a task to mark it done; click a finished one to undo.
- Type in the box and press **Enter** to add a task.

**Good to know**
- It sits on the desktop *background*, so any open window covers it. It is visible on an empty desktop.
- It is centred, about a quarter of the way down the screen, in plain light grey on black.
- Tasks are saved in `~/.local/share/wenv-today/tasks.json` as you change them. If that file is ever damaged, a copy
  is set aside and the widget starts with an empty list.
- It is deliberately simple: no due dates, no editing, no deleting. Finished tasks from earlier days drop out of
  view (they stay in the file).
- "Active time" comes from the recorder described next; if that is not running, the figure is simply left out.

---

## Measuring where your time goes

You cannot tell whether a change helped without a record. This project installs [ActivityWatch](https://activitywatch.net),
which quietly notes which program is in front of you and whether you are at the keyboard.

- **Everything stays on your machine.** It talks only to itself (`127.0.0.1`), and the data is in your home folder.
- **It restarts itself.** On GNOME's Wayland the window recorder can stop silently, which once cost about a day and a
  half of data. A small check every five minutes notices and restarts it.
- **It installs without administrator rights**, into your own folder, from downloads whose fingerprints are checked.

### The hourly report
Every hour a plain-text report is written to `~/work-environment-backup/reports/latest.md` (with dated copies
beside it). You can also run it any time with `python3 report.py`. It states, over the period since you began:

| Figure | Meaning |
|---|---|
| **Active time** | Time with a window in front of you, minus time you were away. |
| **Browser time** | Active time spent in a web browser. |
| **Entertainment time** | Steam or Spotify, plus browser time whose window title looks like YouTube, Netflix, Twitch, Reddit, anime and the like. *This one is a guess from titles.* |
| **Work-app time** | Time in an editor, terminal, office or note-taking app. |
| **Browser launches** | How often you opened a browser from something else. |
| **Task switches** | How often you changed from one app to another. |
| **Work sessions** | Stretches of mostly-active work: how many, the median length, the longest. |
| **Away periods** | Breaks of a minute or more, and their total. |
| **Top apps** | The eight apps that held you longest. |

> No "before" measurement was taken, because the environment was switched on the same day the recorder started.
> The numbers describe what happens now; they cannot prove a change from before.

---

## Safety nets

The environment edits real settings, so it is built to be reversible and to check itself.

- **A backup comes first.** Before any change, the original value of every setting is saved to
  `~/work-environment-backup/`, along with a full dump of your settings and your autostart folder. If you add
  settings later, only the new ones are recorded; the originals are never overwritten.
- **One command puts it all back.** `./restore.sh` returns every recorded setting to its original value.
  `./restore.sh --purge` also removes the extensions and ActivityWatch it installed (your task list and your
  recorded data are kept).
- **A health check.** `./verify.sh` checks every setting, every extension, the widget, the recorder and the
  vault's locks, and ends with "N passed, M failed". `./verify.sh vault` checks only the vault's locks.
- **Downloads are verified.** Every third-party program is fetched from a pinned address and compared with a
  recorded SHA-256 fingerprint. A mismatch stops the install.
- **Two diagnostic tools** exist because of a login problem that once appeared on this laptop:
  `login-probe.sh` records the shell's health for five minutes after log-in, and `freeze-snapshot.sh` takes a
  read-only snapshot you can capture while the screen is frozen.

---

## The Vault

The vault is the "one deliberate step". It is a short quiz that stands between you and the apps most likely to eat
your evening.

### In one sentence
**On weekdays, opening a vault app (by default Steam and its games, and Firefox) asks you to pass a 30-question
quiz first. On Saturday and Sunday it asks for nothing.**

### What it feels like

1. **It is a weekday and you open Steam.** A quiz window opens before the app does.
2. **30 questions, one at a time.** Arithmetic, logical reasoning, reading, school maths and science, and
   data tables. There is no time limit. You need **24 right** (80%).
3. **If you do not pass,** you see only your score. The *same* questions come back, with the answer options
   shuffled, and you try again as often as you like. The correct answers are not revealed, so you cannot just
   read them off.
4. **If you pass,** you see a review of every question with the right answer and an explanation where one exists,
   and **every vault app is unlocked until midnight** (plus a ten-minute grace). The questions you used will not
   appear again until the bank has run out of unused ones.
5. **Ten minutes before midnight** a small window warns you that access is ending, so you can save your work.
6. **If you start an app another way** (from a terminal, a link, or a leftover process), it is closed within a few
   seconds, the quiz opens, and after you pass the app is started again for you.

The vault never closes your terminals, editors, the desktop itself, or the tools you work with.

### What things cost

| You want to… | Cost |
|---|---|
| Open a vault app on a weekday | the 30-question quiz |
| Open a vault app on Saturday or Sunday | nothing |
| **Put another app** in the vault | nothing |
| Turn the vault's enforcement **on** | nothing |
| **Take an app out** of the vault | a 60-question quiz |
| Turn enforcement **off** | a 120-question quiz |
| Change the environment's **locked settings** | a 120-question quiz |
| **Uninstall** the vault | a 120-question quiz |
| Edit the scripts and files in this folder | nothing (debugging and fixing are free) |

The pattern is deliberate: **tightening is free, loosening is expensive.** It is easy to make things harder on
yourself and hard to make them easier in a weak moment.

### The three modes
- **Practice** (`wenv-vault gate --practice`): try the quiz window with nothing at stake. *Passing a practice quiz
  still uses up those questions, because their answers were shown.*
- **Dry run** (how it installs): the vault watches and writes down what it *would* have blocked, but closes nothing
  and shows nothing. Use it for a few days to see whether it matches your habits.
- **Enforcing:** the vault acts. You switch to this with `wenv-vault enforce on`.

If anything is wrong with the vault (its settings are unreadable, or the question bank is empty), it **does not
block anything** and says so in `wenv-vault status`. It is built to fail towards letting you work.

### How it is configured
Most of the behaviour is fixed on purpose, so it cannot be argued with at 11 p.m.

| What | Where it lives | How it changes |
|---|---|---|
| **Which apps are in the vault** | the vault's settings file (written only by the vault service) | `wenv-vault add` (free), `wenv-vault remove` (60 questions) |
| **Enforcement on or off** | the same file | `wenv-vault enforce on` (free), `off` (120 questions) |
| Quiz length (30 / 60 / 120), pass mark (80%), free days (Sat, Sun), grace (10 min), unlock window (20 min) | built into the program | by editing the source and reinstalling |

**Adding an app** takes one line. For example, to put Discord in the vault and have its launcher ask first:

```
wenv-vault add discord --desktop discord
```

An app is recognised by its program name, the folder it runs from, or a marker it leaves in its environment (this is
how Steam *games* are caught even when the Steam window is closed). Shells, terminals, editors and the desktop itself
are on a built-in protected list that can never be vaulted, so a mistaken rule cannot lock you out of your own tools.

### The questions
The bank holds **18,817 questions** from three kinds of source:

- **About a third generated by this project**: percentages, ratios, speed and distance, work-and-time, ages, profit
  and loss, averages, calendar and clock puzzles, probability, number series, syllogisms, letter codes, directions,
  family relationships and table reading. Their answers are computed, so they are always right and never run out.
- **Public exam-style datasets**: AQuA-RAT (numerical word problems), LogiQA (logical reasoning from civil-service
  exams), ARC and SciQ (school science), MMLU (high-school maths and science), RACE (reading comprehension).
- **What is not there yet:** questions on emotional intelligence or situational judgement. The bank has none.

Honest quality notes: the logic questions were machine-translated and are sometimes awkward, and the numerical word
problems are noisy even after filtering. Treat the quiz as friction first and an exam second.

The bank is **not** part of this repository. The licences of several datasets forbid redistributing them, and
publishing the bank would publish the answers. A script builds it on your own machine.

### Protecting the environment itself
Two optional steps extend the vault from "the apps" to "the environment".

**1. The settings lock.** `wenv-vault settings init` locks 32 of the environment's settings (the colours, the
hidden panel, the silence) so that neither you nor any program can change them without the 120-question quiz. To
change one on purpose:

```
wenv-vault settings unlock   # the 120-question quiz; opens a 20-minute window
# ...edit lib.sh, run ./apply.sh all...
wenv-vault settings lock     # keep the change
```

If you do not run the last line, the old values return when the window ends, so a forgotten unlock can never leave
the environment open. Two settings stay unlocked on purpose: the list of enabled extensions, and the grayscale
filter's own switch, because `wenv-color` needs them.

**2. The agent fence.** AI coding assistants can run commands as you. `install-agent-fence.sh` installs rules for
Claude Code that make new sessions refuse to touch the vault's files, to run `sudo`, or to change the vault
(it may still *look*: `status`, `apps`, `log`). This fence is advisory: the real protection is that the vault's
files are owned by the administrator and the settings are locked.

**A third, separate step** (`docker-off.sh`) removes your user from the `docker` group. Membership of that group
is equivalent to having administrator rights without a password, which would let any program running as you undo
the vault. Afterwards you use `sudo docker`.

---

## Installing on a new laptop

This takes the project from nothing to a working environment. Run everything in a terminal **inside the desktop
session** (not over SSH).

### Before you start
- **Zorin OS 18** (Core or Pro) with the standard GNOME desktop, on a 64-bit Intel/AMD machine. Other distributions
  may work but the theme names and extension identifiers are Zorin's.
- A normal user with `sudo` rights, and internet access.
- *For the vault only:* Steam and/or Firefox installed, if you want them in it.

### Part 1: the quiet desktop

```bash
# 1. Get the project (any folder works; ~/work-environment is the convention)
git clone https://github.com/swarajbari18/work-environment.git ~/work-environment
cd ~/work-environment

# 2. Download and install the components (no administrator rights needed)
./install-components.sh

# 3. Apply the environment: settings, widget, hourly report and recorder watchdog
./apply.sh all
```

4. **Log out and log back in.** This is required: GNOME on Wayland only discovers new extensions at log-in.
5. Check everything took:

```bash
./verify.sh          # expect "N passed, 0 failed"
./apply.sh status    # expect no lines marked DIFF
./wenv-color status  # expect: State: ACTIVE
```

You should now see a black, grayscale desktop with the Today widget, and no top bar or dock.

> `./apply.sh all` saves your original settings first. If you dislike the result, `./restore.sh` undoes it.

### Part 2: the vault (optional)

```bash
# 6. Build the question bank on this machine (needs internet; a few minutes)
sudo apt install python3-venv        # only if the next command says it is missing
./vault/fetch_bank.sh ~/wenv-bank

# 7. Install the vault service (asks for your password; starts in DRY RUN)
sudo ./vault/install.sh --bank ~/wenv-bank/bank --keep-bank

# 8. Install the parts that live in your session: commands, launchers, the desktop agent
./vault/install-user.sh
```

Log out and in once more so the new `wenv-vault` command is on your path. (To update later, `git pull` and run
`sudo ./vault/install.sh` again with no options: it reuses the installed question bank and keeps your policy.)
Then:

```bash
wenv-vault status               # shows "dry-run", the apps, and the bank size
wenv-vault gate --practice      # try the quiz window; nothing is at stake
```

Use it in dry-run for a few days. Look at `wenv-vault log` to see what it *would* have blocked. When you are happy:

```bash
wenv-vault enforce on           # free; turning it off again costs the 120-question quiz
```

> **Want to see the quiz before installing anything?** Run `./vault/dev-demo.sh`. It starts a throwaway copy,
> opens a practice quiz, and cleans up when you close it.

### Part 3: protecting the environment (optional, do these last)

```bash
wenv-vault settings init             # lock the environment's settings
sudo ./vault/install-agent-fence.sh  # keep AI assistants away from the vault (start a new session after)
sudo ./vault/docker-off.sh           # leave the docker group (log out and in afterwards)
```

Do these last, and not from inside an AI assistant session: after the fence is in place, those sessions are
refused `sudo`.

### A note on administrator scripts
`vault/install.sh`, `vault/install-agent-fence.sh`, `vault/docker-off.sh` and `vault/uninstall.sh` run with
administrator rights and change system files. They were written for Zorin OS 18 on GNOME 46. Read them before you
run them; each begins with a plain description of what it does.

### What not to copy from the old laptop
Do not copy `~/work-environment-backup` (it holds the *old* machine's original settings, which a restore would then
apply) or `~/.config/activitywatch` (it contains paths from the old account).

### The first ten minutes: how to know it worked

```bash
./apply.sh status | grep -c DIFF                        # 0
gnome-extensions list --enabled                         # Just Perfection, Colorblind Filters, Focused Window D-Bus, WENV Today
pgrep -x aw-tauri; pgrep -x aw-awatcher                 # each prints a number
systemctl --user is-active wenv-aw-watchdog.timer       # active
systemctl --user is-active wenv-report.timer            # active
systemctl is-active wenv-vaultd                         # active    (vault only)
wenv-vault status                                       # dry-run   (vault only)
```

---

## Everyday commands

### The environment
| Command | What it does |
|---|---|
| `./verify.sh` | Check that everything is as it should be. |
| `./apply.sh status` | Compare every setting with its target. |
| `./apply.sh all` | (Re)apply the whole environment. Re-enables grayscale. |
| `./wenv-color off` / `on` / `status` | Colour for a while / grayscale again / which is it? |
| `python3 report.py` | Print the time report now. |
| `./backup.sh` | Record any newly added settings (run automatically before every change). |
| `./restore.sh` | Put every setting back as it was originally. |

### The vault
| Command | What it does |
|---|---|
| `wenv-vault status` | The mode, today's state, the apps, tickets and the bank size. |
| `wenv-vault apps` | List the apps and the rules that recognise them. |
| `wenv-vault log` | What the vault saw and did. |
| `wenv-vault unlock` | Take today's quiz now. |
| `wenv-vault gate --practice` | Practise the quiz; changes nothing. |
| `wenv-vault add NAME …` | Put an app in the vault (free). |
| `wenv-vault remove NAME` | Take an app out (60 questions). |
| `wenv-vault enforce on` / `off` | Start enforcing (free) / stop (120 questions). |
| `wenv-vault settings status` | Are the environment's settings locked? |
| `wenv-vault settings init` / `lock` | Lock them (free). |
| `wenv-vault settings unlock` | Open a 20-minute window to change them (120 questions). |
| `wenv-vault uninstall` | Earn permission to remove the vault (120 questions). |

---

## Known limits and honest caveats

- **The sign-in screen is not black.** It belongs to a different system service. Several attempts failed, and it was
  left alone.
- **Grayscale on Wayland has a known weak point** (windows drawn directly by the graphics driver). A guard is in
  place and was reasoned from the source, but a screen-level confirmation on every machine is still missing.
- **You can always undo it.** You have `sudo`; that was a deliberate choice, because the goal is friction, not
  security against yourself. Removing the vault the hard way costs several deliberate commands, not a quiz.
- **A chatbot can answer the quiz.** Nothing prevents pasting a question into one.
- **Chrome is deliberately not in the vault**, because automated tools use it. A determined procrastinator can
  browse in Chrome; narrowing that is a decision for you.
- **Python- and Node-based apps cannot be put in the vault.** Programs with those names are protected so a rule
  can never close your own tools.
- **Firefox is recognised only in the usual places** (the Ubuntu snap or `/usr/lib/firefox`). A Flatpak or a
  downloaded copy would not be.
- **There are no emotional or situational questions yet.** The bank is arithmetic, logic, reading and school
  science.
- **No before-and-after proof.** The recorder and the environment started together, so the report cannot show a
  change from a baseline.
- **Whether the quiz window takes focus over a running full-screen game** was not verified on a real game.
- **A login freeze once occurred** on the X11 session. It did not recur on Wayland, and its root cause was never
  found; the diagnostic tools above remain for that reason.
- **Tested on one machine.** It was built and checked on a single Zorin OS 18.1 laptop. The automated tests
  (several hundred of them, including real process-closing and a real quiz window drawn headlessly) cover the logic
  well, but nothing replaces a second laptop. If you try this on one, you will find things.

---

## Taking it all back out

| To remove… | Do this |
|---|---|
| Colour only, for now | `./wenv-color off` |
| The desktop changes | `./restore.sh` then log out and in |
| …plus the extensions and recorder it installed | `./restore.sh --purge` |
| The vault's launcher changes and agent | `./vault/install-user.sh --remove` |
| The agent fence | `sudo ./vault/install-agent-fence.sh --remove` |
| The vault service itself | `wenv-vault uninstall` (120 questions), then `sudo /opt/wenv-vault/uninstall.sh` |
| The docker restriction | `sudo usermod -aG docker $USER`, then log out and in |

Removing the vault service also removes the settings locks and the agent fence, so they can never be left behind
with nothing able to lift them. While the settings are locked, `./restore.sh` will tell you what to run instead
of failing key by key.

---

## What is in this folder

| Path | Purpose |
|---|---|
| `install-components.sh` | Downloads and installs the extensions and ActivityWatch, with hash checks. |
| `apply.sh`, `lib.sh` | Applies the environment in stages, and holds the list of settings. |
| `backup.sh`, `restore.sh` | Saves your originals, and puts them back. |
| `verify.sh` | The health check. |
| `wenv-color` | Switches colour on and off. |
| `report.py`, `test_report.py` | The time report and its tests. |
| `aw-watchdog.sh`, `systemd/` | Keeps the recorder running; the timers that run it and the report. |
| `login-probe.sh`, `freeze-snapshot.sh` | Diagnostic tools for a login problem. |
| `today/` | The Today widget and its tests. |
| `vault/` | The vault: the quiz, the service, the installers and their tests. `vault/DESIGN.md` is the technical reference. |
| `THIRD_PARTY.md` | Everything this project downloads, and under which licences. |
| `downloads/` | Where the third-party downloads are cached (not committed). |

---

## Credits and licences

This project stands on other people's work, and downloads rather than bundles it: GNOME Shell extensions
*Just Perfection*, *Colorblind Filters* and *Focused Window D-Bus*; *ActivityWatch* and *awatcher*; and the public
datasets behind the question bank. Full details and licences are in [`THIRD_PARTY.md`](THIRD_PARTY.md).

The original code in this repository is released under the [MIT licence](LICENSE). That covers this project's own
code only; every third-party component keeps its own licence, as listed in `THIRD_PARTY.md`.

> **Use it at your own risk.** A few of the scripts run with administrator rights and edit system files. They were
> written for one Zorin OS 18.1 laptop. Read what you are about to run.
