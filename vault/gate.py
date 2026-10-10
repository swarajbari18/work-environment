"""The quiz window.

GateModel is plain Python (testable without a display): it fetches a set from the daemon, keeps the answers,
saves a draft so a crash does not lose 100 answers, submits, and handles the retry. The GTK4 view below is a thin
shell over it. Only the score is shown after a fail; the review (answers, explanations) only after a pass.
"""
import hashlib
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import client as vclient  # noqa: E402

LETTERS = "ABCDE"


def draft_dir():
    return os.environ.get("WENV_VAULT_DRAFTS") or os.path.join(
        os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "wenv-vault")


class GateModel:
    def __init__(self, vault, tier, practice=False, drafts=None):
        self.vault = vault
        self.tier = tier
        self.practice = practice
        self.drafts = drafts or draft_dir()
        self.state = "loading"           # loading | quiz | result | error
        self.error = None
        self.questions = []
        self.needed = 0
        self.tries = 0
        self.answers = []
        self.index = 0
        self.result = None

    # --- loading and drafts -------------------------------------------------------------------------------------
    def _draft_path(self):
        return os.path.join(self.drafts, f"draft-{'practice-' if self.practice else ''}{self.tier}.json")

    def _fingerprint(self):
        ids = "|".join(q["id"] for q in self.questions)
        return hashlib.sha1(f"{self.tries}|{ids}".encode()).hexdigest()

    def load(self):
        op = "practice_present" if self.practice else "present"
        try:
            reply = self.vault.call(op, tier=self.tier)
        except vclient.VaultUnavailable as exc:
            return self._fail(str(exc))
        if not reply.get("ok"):
            return self._fail(reply.get("message") or reply.get("error") or "the vault refused")
        self.questions = reply["questions"]
        self.needed = reply["needed"]
        self.tries = reply["tries"]
        self.answers = [None] * len(self.questions)
        self.index = 0
        self.result = None
        self._restore_draft()
        self.state = "quiz"
        return True

    def _fail(self, message):
        self.state, self.error = "error", message
        return False

    def _restore_draft(self):
        try:
            with open(self._draft_path(), encoding="utf-8") as fh:
                draft = json.load(fh)
            if draft.get("fingerprint") == self._fingerprint() and len(draft.get("answers", [])) == len(self.answers):
                self.answers = [a if isinstance(a, int) else None for a in draft["answers"]]
                self.index = min(max(int(draft.get("index", 0)), 0), len(self.questions) - 1)
        except (OSError, ValueError, TypeError):
            pass

    def _save_draft(self):
        try:
            os.makedirs(self.drafts, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.drafts, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"fingerprint": self._fingerprint(), "answers": self.answers, "index": self.index}, fh)
            os.replace(tmp, self._draft_path())
        except OSError:
            pass                           # a draft is a convenience, never a reason to fail

    def _clear_draft(self):
        try:
            os.unlink(self._draft_path())
        except OSError:
            pass

    # --- navigation and answering -------------------------------------------------------------------------------
    @property
    def total(self):
        return len(self.questions)

    @property
    def current(self):
        return self.questions[self.index]

    def select(self, option, index=None):
        i = self.index if index is None else index
        if option is not None and not 0 <= option < len(self.questions[i]["options"]):
            return False
        self.answers[i] = option
        self._save_draft()
        return True

    def go(self, index):
        self.index = min(max(index, 0), self.total - 1)
        self._save_draft()

    def next(self):
        self.go(self.index + 1)

    def prev(self):
        self.go(self.index - 1)

    def unanswered(self):
        return [i for i, a in enumerate(self.answers) if a is None]

    def next_unanswered(self):
        pending = self.unanswered()
        if not pending:
            return None
        later = [i for i in pending if i > self.index]
        self.go(later[0] if later else pending[0])
        return self.index

    # --- submitting ---------------------------------------------------------------------------------------------
    def submit(self):
        op = "practice_submit" if self.practice else "submit"
        try:
            reply = self.vault.call(op, tier=self.tier, answers=self.answers)
        except vclient.VaultUnavailable as exc:
            return self._fail(str(exc))
        if not reply.get("ok"):
            return self._fail(reply.get("message") or reply.get("error") or "the vault refused")
        self.result = reply
        self.state = "result"
        if reply["passed"]:
            self._clear_draft()
        return reply["passed"]

    def retry(self):
        """After a fail: the same questions come back with the options reshuffled."""
        self._clear_draft()
        return self.load()


# --------------------------------------------------------------------------------------------------------------
# GTK4 view
# --------------------------------------------------------------------------------------------------------------
CSS = b"""
.vault-title { font-size: 18pt; font-weight: bold; }
.vault-progress { opacity: 0.7; }
.vault-passage { font-family: monospace; padding: 12px; background: alpha(currentColor, 0.07); border-radius: 6px; }
.vault-stem { font-size: 14pt; }
.vault-option { padding: 6px 4px; font-size: 13pt; }
.vault-good { color: #8fd18f; }
.vault-bad { color: #e08a8a; }
.vault-explain { opacity: 0.75; }
.vault-big { font-size: 22pt; font-weight: bold; }
"""


def _load_gtk():
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk, Gio, GLib, Gtk, Pango
    return Gtk, Gdk, Gio, GLib, Pango


class GateApp:
    """Runs the model in a window. `run()` returns True if the quiz was passed."""

    def __init__(self, model, title="Vault", app_label=None, auto_close=None, unique=True, app_id="local.wenv.Gate"):
        self.Gtk, self.Gdk, self.Gio, self.GLib, self.Pango = _load_gtk()
        self.model = model
        self.title = title
        self.app_label = app_label
        self.auto_close = auto_close
        self.passed = False
        flags = self.Gio.ApplicationFlags.FLAGS_NONE if unique else self.Gio.ApplicationFlags.NON_UNIQUE
        self.app = self.Gtk.Application(application_id=app_id, flags=flags)
        self.app.connect("activate", self._activate)
        self.window = None
        self.option_buttons = []

    def run(self):
        self.app.run([])
        return self.passed

    # --- construction -------------------------------------------------------------------------------------------
    def _activate(self, app):
        Gtk = self.Gtk
        if self.window is not None:
            self.window.present()
            return
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(self.Gdk.Display.get_default(), provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        win = Gtk.ApplicationWindow(application=app, title=self.title)
        win.set_default_size(980, 720)
        self.window = win

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for side in ("start", "end", "top", "bottom"):
            getattr(root, f"set_margin_{side}")(18)
        win.set_child(root)

        head = Gtk.Box(spacing=12)
        self.title_label = Gtk.Label(xalign=0, hexpand=True)
        self.title_label.add_css_class("vault-title")
        self.progress_label = Gtk.Label(xalign=1)
        self.progress_label.add_css_class("vault-progress")
        head.append(self.title_label)
        head.append(self.progress_label)
        root.append(head)

        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        root.append(self.stack)

        # quiz page
        scroller = Gtk.ScrolledWindow(vexpand=True)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        scroller.set_child(body)
        self.passage_label = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.passage_label.add_css_class("vault-passage")
        self.stem_label = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.stem_label.add_css_class("vault-stem")
        self.options_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for w in (self.passage_label, self.stem_label, self.options_box):
            body.append(w)
        self.stack.add_named(scroller, "quiz")

        # result page
        result_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.result_label = Gtk.Label(xalign=0, wrap=True)
        self.result_label.add_css_class("vault-big")
        self.result_note = Gtk.Label(xalign=0, wrap=True)
        review_scroll = Gtk.ScrolledWindow(vexpand=True)
        self.review_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        review_scroll.set_child(self.review_box)
        for w in (self.result_label, self.result_note, review_scroll):
            result_box.append(w)
        self.stack.add_named(result_box, "result")

        # message page (loading / error)
        self.message_label = Gtk.Label(wrap=True, vexpand=True)
        self.stack.add_named(self.message_label, "message")

        # footer
        foot = Gtk.Box(spacing=8)
        self.back_btn = Gtk.Button(label="Back")
        self.next_btn = Gtk.Button(label="Next")
        self.pending_btn = Gtk.Button(label="Next unanswered")
        spacer = Gtk.Box(hexpand=True)
        self.submit_btn = Gtk.Button(label="Submit")
        self.submit_btn.add_css_class("suggested-action")
        self.retry_btn = Gtk.Button(label="Try again")
        self.close_btn = Gtk.Button(label="Close")
        for w in (self.back_btn, self.next_btn, self.pending_btn, spacer, self.submit_btn, self.retry_btn,
                  self.close_btn):
            foot.append(w)
        root.append(foot)
        self.back_btn.connect("clicked", lambda *_: self._navigate(-1))
        self.next_btn.connect("clicked", lambda *_: self._navigate(+1))
        self.pending_btn.connect("clicked", lambda *_: self._to_pending())
        self.submit_btn.connect("clicked", lambda *_: self._submit())
        self.retry_btn.connect("clicked", lambda *_: self._retry())
        self.close_btn.connect("clicked", lambda *_: self.window.close())

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        win.add_controller(keys)

        self.title_label.set_text(self.title)
        self._show_message("Loading questions...")
        self.GLib.idle_add(self._load)
        win.present()

    # --- behaviour ----------------------------------------------------------------------------------------------
    def _load(self):
        if self.model.load():
            self._render_question()
        else:
            self._show_error()
        return False

    def _set_buttons(self, back=False, nxt=False, pending=False, submit=False, retry=False, close=True):
        for btn, on in ((self.back_btn, back), (self.next_btn, nxt), (self.pending_btn, pending),
                        (self.submit_btn, submit), (self.retry_btn, retry), (self.close_btn, close)):
            btn.set_visible(on)

    def _show_message(self, text):
        self.message_label.set_text(text)
        self.stack.set_visible_child_name("message")
        self.progress_label.set_text("")
        self._set_buttons()

    def _show_error(self):
        self._show_message(f"The vault could not be reached.\n\n{self.model.error}")

    def _render_question(self):
        m = self.model
        q = m.current
        self.stack.set_visible_child_name("quiz")
        left = len(m.unanswered())
        self.progress_label.set_text(f"{m.index + 1} / {m.total}    {left} unanswered    {m.needed} correct to pass")
        passage = q.get("passage")
        self.passage_label.set_visible(bool(passage))
        self.passage_label.set_text(passage or "")
        self.stem_label.set_text(q["stem"])
        child = self.options_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.options_box.remove(child)
            child = nxt
        self.option_buttons = []
        group = None
        for i, text in enumerate(q["options"]):
            btn = self.Gtk.CheckButton(label=f"{LETTERS[i]}.  {text}")
            btn.add_css_class("vault-option")
            if group is None:
                group = btn
            else:
                btn.set_group(group)
            btn.set_active(m.answers[m.index] == i)
            btn.connect("toggled", self._on_toggled, i)
            self.options_box.append(btn)
            self.option_buttons.append(btn)
        last = m.index == m.total - 1
        self._set_buttons(back=m.index > 0, nxt=not last, pending=left > 0, submit=True)

    def _on_toggled(self, button, option):
        if button.get_active() and self.model.state == "quiz":
            self.model.select(option)
            left = len(self.model.unanswered())
            self.progress_label.set_text(
                f"{self.model.index + 1} / {self.model.total}    {left} unanswered    "
                f"{self.model.needed} correct to pass")
            self.pending_btn.set_visible(left > 0)

    def _navigate(self, step):
        if self.model.state != "quiz":
            return
        self.model.go(self.model.index + step)
        self._render_question()

    def _to_pending(self):
        if self.model.state == "quiz" and self.model.next_unanswered() is not None:
            self._render_question()

    def _on_key(self, _ctrl, keyval, _code, _state):
        Gdk = self.Gdk
        if self.model.state != "quiz":
            return False
        name = Gdk.keyval_name(keyval) or ""
        n = len(self.option_buttons)
        if name.isdigit() and 1 <= int(name) <= n:
            self.option_buttons[int(name) - 1].set_active(True)
            return True
        if len(name) == 1 and name.upper() in LETTERS[:n]:
            self.option_buttons[LETTERS.index(name.upper())].set_active(True)
            return True
        if name in ("Right", "Return", "KP_Enter"):
            self._navigate(+1)
            return True
        if name == "Left":
            self._navigate(-1)
            return True
        return False

    def _submit(self):
        self._show_message("Checking...")
        self.GLib.idle_add(self._do_submit)

    def _do_submit(self):
        passed = self.model.submit()
        if self.model.state == "error":
            self._show_error()
            self._set_buttons(close=True)
            return False
        self._render_result(passed)
        return False

    def _render_result(self, passed):
        m = self.model
        r = m.result
        self.stack.set_visible_child_name("result")
        self.progress_label.set_text("")
        child = self.review_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.review_box.remove(child)
            child = nxt
        if passed:
            self.passed = True
            self.result_label.set_text(f"Passed: {r['score']} / {r['total']}")
            what = "Practice only: nothing was unlocked." if m.practice else self._unlock_text()
            self.result_note.set_text(what)
            for n, item in enumerate(r.get("review") or [], 1):
                self.review_box.append(self._review_row(n, item))
            self._set_buttons(close=True)
            self.close_btn.set_label(f"Open {self.app_label}" if self.app_label else "Close")
            if self.auto_close:
                self.GLib.timeout_add(int(self.auto_close * 1000), lambda: (self.window.close(), False)[1])
        else:
            self.result_label.set_text(f"{r['score']} / {r['total']}")
            self.result_note.set_text(
                f"You need {r['needed']} correct. The same questions are coming back, with the options "
                "shuffled. The answers are not shown until you pass.")
            self._set_buttons(retry=True, close=True)
            self.close_btn.set_label("Close")

    def _unlock_text(self):
        return {"launch": "Unlocked until midnight.",
                "remove_app": "You can remove one app from the vault for the next 10 minutes.",
                "change_settings": "You can change the vault settings for the next 10 minutes."}[self.model.tier]

    def _review_row(self, n, item):
        Gtk = self.Gtk
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        stem = Gtk.Label(xalign=0, wrap=True, selectable=True, label=f"{n}.  {item['stem']}")
        verdict = Gtk.Label(xalign=0, wrap=True, selectable=True)
        if item["ok"]:
            verdict.set_text(f"Correct: {item['correct']}")
            verdict.add_css_class("vault-good")
        else:
            given = item["given"] if item["given"] is not None else "(no answer)"
            verdict.set_text(f"Your answer: {given}\nCorrect: {item['correct']}")
            verdict.add_css_class("vault-bad")
        box.append(stem)
        box.append(verdict)
        if item.get("explanation"):
            note = Gtk.Label(xalign=0, wrap=True, selectable=True, label=item["explanation"])
            note.add_css_class("vault-explain")
            box.append(note)
        return box

    def _retry(self):
        self._show_message("Loading the same questions...")
        self.GLib.idle_add(self._load)


def run_gate(vault, tier, practice=False, app_label=None, auto_close=None, unique=True):
    title = {"launch": "Vault: unlock for today", "remove_app": "Vault: remove an app",
             "change_settings": "Vault: change settings"}.get(tier, "Vault")
    if practice:
        title += " (practice)"
    model = GateModel(vault, tier, practice=practice)
    return GateApp(model, title=title, app_label=app_label, auto_close=auto_close, unique=unique).run()


def show_notice(title, message, seconds=None, unique=True):
    """A plain message window (notifications are off in this environment). Blocks until it is closed."""
    Gtk, Gdk, Gio, GLib, Pango = _load_gtk()
    flags = Gio.ApplicationFlags.FLAGS_NONE if unique else Gio.ApplicationFlags.NON_UNIQUE
    app = Gtk.Application(application_id="local.wenv.Notice", flags=flags)

    def activate(a):
        win = Gtk.ApplicationWindow(application=a, title=title)
        win.set_default_size(520, 220)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        for side in ("start", "end", "top", "bottom"):
            getattr(box, f"set_margin_{side}")(22)
        head = Gtk.Label(label=title, xalign=0)
        head.add_css_class("title-2")
        body = Gtk.Label(label=message, xalign=0, wrap=True, vexpand=True)
        ok = Gtk.Button(label="OK")
        ok.connect("clicked", lambda *_: win.close())
        for w in (head, body, ok):
            box.append(w)
        win.set_child(box)
        win.present()
        if seconds:
            GLib.timeout_add(int(seconds * 1000), lambda: (win.close(), False)[1])

    app.connect("activate", activate)
    app.run([])


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Open the vault quiz window (or a notice).")
    ap.add_argument("--tier", default="launch", choices=["launch", "remove_app", "change_settings"])
    ap.add_argument("--practice", action="store_true")
    ap.add_argument("--app", default=None)
    ap.add_argument("--socket", default=None)
    ap.add_argument("--notice", default=None, help="show a message window with this title instead of a quiz")
    ap.add_argument("--message", default="")
    ap.add_argument("--seconds", type=float, default=None)
    args = ap.parse_args()
    if args.notice:
        show_notice(args.notice, args.message, args.seconds)
        sys.exit(0)
    ok = run_gate(vclient.VaultClient(args.socket), args.tier, practice=args.practice, app_label=args.app)
    sys.exit(0 if ok else 1)
