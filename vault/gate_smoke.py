"""GTK smoke test, run as a subprocess by test_gate_gtk.py under the Broadway backend. Prints one JSON object."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate  # noqa: E402
from test_gate import FakeVault  # noqa: E402

scenario = sys.argv[1]
out = {"scenario": scenario, "steps": []}
tmp = tempfile.mkdtemp()
vault = FakeVault(n=3)
model = gate.GateModel(vault, "launch", drafts=tmp)
app = gate.GateApp(model, title="Vault smoke", app_label="Firefox", unique=False, app_id="local.wenv.GateSmoke")
GLib = app.GLib
Gdk = app.Gdk
state = {"step": 0}


def note(name, **kw):
    out["steps"].append({"step": name, **kw})


def key(name):
    return app._on_key(None, Gdk.keyval_from_name(name), 0, 0)


def labels_text():
    return {"title": app.title_label.get_text(), "progress": app.progress_label.get_text(),
            "stem": app.stem_label.get_text(), "result": app.result_label.get_text(),
            "note": app.result_note.get_text()}


def finish(code=0):
    out["close_label"] = app.close_btn.get_label()
    out["passed_flag"] = app.passed
    out["calls"] = [c[0] for c in vault.calls]
    app.window.close()
    return False


def step():
    s = state["step"]
    try:
        if s == 0:
            if model.state != "quiz":
                return True                                     # still loading; try again on the next tick
            note("loaded", **labels_text(), options=[b.get_label() for b in app.option_buttons],
                 passage_visible=app.passage_label.get_visible(), submit_visible=app.submit_btn.get_visible(),
                 back_visible=app.back_btn.get_visible())
            right = scenario == "pass"
            app.option_buttons[1 if right else 0].set_active(True)      # a click on option B (or A)
            note("clicked", answers=list(model.answers))
            app.next_btn.emit("clicked")
            note("next", index=model.index, **labels_text())
            key("2" if right else "1")                           # keyboard: answer 2 = option B, 1 = option A
            key("Right")
            key("b" if right else "a")                           # keyboard letter
            note("keys", answers=list(model.answers), index=model.index, unanswered=model.unanswered())
            app.submit_btn.emit("clicked")
        elif s == 1:
            if model.state not in ("result", "error"):
                return True
            note("result", state=model.state, **labels_text(), retry_visible=app.retry_btn.get_visible(),
                 review_rows=len((model.result or {}).get("review") or []))
            if scenario == "fail":
                app.retry_btn.emit("clicked")
        elif s == 2:
            if scenario == "fail":
                if model.state != "quiz":
                    return True
                note("retried", tries=model.tries, answers=list(model.answers), **labels_text())
            return finish()
        else:
            return finish()
    except Exception as exc:                                     # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
        return finish()
    state["step"] += 1
    return True


GLib.timeout_add(150, step)
GLib.timeout_add(25000, lambda: (out.setdefault("error", "timeout"), app.window.close(), False)[2])
app.run()
print(json.dumps(out))
