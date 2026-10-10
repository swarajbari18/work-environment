#!/usr/bin/env python3
"""The vault daemon: grades quizzes, holds the answer key, and (when enforcing) stops locked apps.

  vaultd.py [--root DIR] [--owner-uid N] [--interval S] [--socket PATH] [--selftest]

--root stages everything under DIR (tests, dev runs); in production it is empty and paths are /etc, /var/lib, /run.
SIGHUP reloads the policy and the bank. SIGTERM / SIGINT stop it cleanly.
"""
import argparse
import json
import os
import signal
import socket
import socketserver
import struct
import sys
import threading
import queue
import select

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402
import enforce  # noqa: E402
import protocol  # noqa: E402
import questions  # noqa: E402

MAX_REQUEST = 1024 * 1024
READ_TIMEOUT = 30.0


class EventBus:
    def __init__(self):
        self._lock = threading.Lock()
        self._queues = set()

    def subscribe(self):
        q = queue.Queue(maxsize=100)
        with self._lock:
            self._queues.add(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            self._queues.discard(q)

    def emit(self, event):
        with self._lock:
            targets = list(self._queues)
        for q in targets:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass                                   # a stalled subscriber must not block the daemon

    def count(self):
        with self._lock:
            return len(self._queues)


def peer_uid(sock):
    creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    _pid, uid, _gid = struct.unpack("3i", creds)
    return uid


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        service, bus = self.server.service, self.server.bus
        try:
            uid = peer_uid(self.request)
        except OSError:
            return
        self.request.settimeout(READ_TIMEOUT)
        while True:
            try:
                line = self.rfile.readline(MAX_REQUEST + 1)
            except (OSError, socket.timeout):
                return
            if not line:
                return
            if len(line) > MAX_REQUEST:
                self._send({"ok": False, "error": "too_large", "message": "request too large"})
                return
            try:
                request = json.loads(line.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._send({"ok": False, "error": "bad_request", "message": "not valid JSON"})
                continue
            if isinstance(request, dict) and request.get("op") == "subscribe":
                if uid not in (0, service.owner_uid):
                    self._send({"ok": False, "error": "forbidden", "message": "only the owner may subscribe"})
                    return
                self._subscribe(bus)
                return
            self._send(service.handle(request, uid))

    def _send(self, reply):
        try:
            self.wfile.write(json.dumps(reply).encode("utf-8") + b"\n")
            self.wfile.flush()
        except OSError:
            pass

    def _subscribe(self, bus):
        q = bus.subscribe()
        try:
            self._send({"ok": True, "subscribed": True})
            self.request.settimeout(None)
            while not self.server.stopping.is_set():
                try:
                    event = q.get(timeout=1.0)
                except queue.Empty:
                    # Notice a client that went away without ever sending anything.
                    readable, _, _ = select.select([self.request], [], [], 0)
                    if readable and not self.request.recv(1, socket.MSG_PEEK):
                        return
                    continue
                try:
                    self.wfile.write(json.dumps(event).encode("utf-8") + b"\n")
                    self.wfile.flush()
                except OSError:
                    return
        finally:
            bus.unsubscribe(q)


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, sock_path, service, bus):
        self.service = service
        self.bus = bus
        self.stopping = threading.Event()
        directory = os.path.dirname(sock_path)
        os.makedirs(directory, exist_ok=True)
        try:
            os.unlink(sock_path)
        except FileNotFoundError:
            pass
        super().__init__(sock_path, _Handler)
        os.chmod(sock_path, 0o666)                    # access is decided by SO_PEERCRED, not by file mode

    def shutdown_all(self):
        self.stopping.set()
        self.shutdown()


def load_service(paths, owner_uid, bus=None, previous=None):
    """Build a Service from disk. Anything unusable is carried as a reason and the service fails open."""
    policy, err = config.load_policy(paths.policy_file)
    bank, problems = questions.load_bank(paths.bank_dir)
    uid = policy["owner_uid"] if policy else owner_uid
    if uid is None:
        raise SystemExit("no owner uid: pass --owner-uid or provide a readable policy")
    emit = bus.emit if bus else None
    if previous is None:
        svc = protocol.Service(paths, bank, policy, uid, emit=emit, load_error=err)
    else:
        svc = previous
        svc.bank, svc.policy, svc.owner_uid, svc.load_error = bank, policy, uid, err
        svc.vault.bank = svc.practice.bank = bank
    for p in problems[:20]:
        svc.log("bank_problem", detail=p)
    return svc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=os.environ.get("WENV_VAULT_ROOT", ""))
    parser.add_argument("--owner-uid", type=int, default=None)
    parser.add_argument("--interval", type=float, default=enforce.INTERVAL)
    parser.add_argument("--socket", default=None)
    parser.add_argument("--selftest", action="store_true", help="load everything, tick once, print a summary, exit")
    args = parser.parse_args(argv)

    paths = config.Paths.from_root(args.root)
    bus = EventBus()
    service = load_service(paths, args.owner_uid, bus)

    if args.selftest:
        enf = enforce.Enforcer(service, kill=lambda pid, sig: None)
        enf.tick()
        reason = service.fail_open_reason()
        print(json.dumps({"apps": [a["name"] for a in service.apps()], "bank": len(service.bank),
                          "enforce": service.enforce, "fail_open": reason, "owner_uid": service.owner_uid}))
        return 1 if reason else 0

    sock_path = args.socket or paths.socket
    enforcer = enforce.Enforcer(service)
    server = Server(sock_path, service, bus)
    stop, thread = enforce.start_thread(enforcer, args.interval)

    def on_hup(*_):
        with service.lock:
            load_service(paths, args.owner_uid, bus, previous=service)
            service.log("reload", enforce=service.enforce, apps=len(service.apps()), bank=len(service.bank))

    def on_term(*_):
        threading.Thread(target=server.shutdown_all, daemon=True).start()

    signal.signal(signal.SIGHUP, on_hup)
    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)
    service.log("start", enforce=service.enforce, apps=len(service.apps()), bank=len(service.bank),
                fail_open=service.fail_open_reason())
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        stop.set()
        thread.join(timeout=3)
        server.server_close()
        try:
            os.unlink(sock_path)
        except OSError:
            pass
        service.log("stop")
    return 0


if __name__ == "__main__":
    sys.exit(main())
