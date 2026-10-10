"""Talk to the vault daemon: one JSON object per line over a unix socket."""
import json
import os
import socket

import config

MAX_LINE = 8 * 1024 * 1024


class VaultUnavailable(Exception):
    """The daemon is not reachable (not installed, stopped, or the socket path is wrong)."""


def default_socket():
    return config.Paths.from_env().socket


class VaultClient:
    def __init__(self, socket_path=None, timeout=15.0):
        self.socket_path = socket_path or default_socket()
        self.timeout = timeout

    def _connect(self, timeout):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(self.socket_path)
        except OSError as exc:
            sock.close()
            raise VaultUnavailable(f"cannot reach the vault at {self.socket_path}: {exc}") from exc
        return sock

    def call(self, op, **fields):
        """Send one request and return the reply dict. Raises VaultUnavailable if the daemon cannot be reached."""
        sock = self._connect(self.timeout)
        try:
            sock.sendall(json.dumps({"op": op, **fields}).encode("utf-8") + b"\n")
            return read_line(sock)
        except (OSError, ValueError) as exc:
            raise VaultUnavailable(f"vault connection failed: {exc}") from exc
        finally:
            sock.close()

    def subscribe(self):
        """Yield events as they arrive. Ends (by raising VaultUnavailable) when the daemon goes away."""
        sock = self._connect(self.timeout)
        try:
            sock.sendall(b'{"op": "subscribe"}\n')
            reader = LineReader(sock)
            reply = reader.read()
            if not reply.get("ok"):
                raise VaultUnavailable(f"subscribe refused: {reply}")
            sock.settimeout(None)
            while True:
                yield reader.read()
        except (OSError, ValueError) as exc:
            raise VaultUnavailable(f"vault connection lost: {exc}") from exc
        finally:
            sock.close()


class LineReader:
    """Reads newline-terminated JSON objects from a socket, keeping bytes that arrive after the first newline."""

    def __init__(self, sock):
        self.sock = sock
        self.buf = b""

    def read(self):
        while b"\n" not in self.buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("connection closed")
            self.buf += chunk
            if len(self.buf) > MAX_LINE and b"\n" not in self.buf:
                raise ValueError("message too large")
        line, _, self.buf = self.buf.partition(b"\n")
        return json.loads(line.decode("utf-8"))


def read_line(sock):
    """One message from a one-shot connection."""
    return LineReader(sock).read()


def socket_exists(path=None):
    return os.path.exists(path or default_socket())
