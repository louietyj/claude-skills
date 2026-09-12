"""Minimal RFC 6455 client, enough for Retell's monitor stream.

Stdlib only on purpose: this runs in the claude.ai sandbox, where a pip install
is one more thing that can fail mid-call. The stream is server-to-client text
frames plus pings, so the client half is a handshake, an unmasking frame reader
and a masked close -- not a websocket library.
"""

import base64
import json
import os
import socket
import ssl
import struct
import time
import urllib.parse

_CONT, _TEXT, _BINARY, _CLOSE, _PING, _PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA


class WSError(Exception):
    pass


class WebSocket:
    def __init__(self, url, protocols=(), timeout=30):
        u = urllib.parse.urlparse(url)
        if u.scheme not in ("ws", "wss"):
            raise WSError(f"not a websocket url: {url}")
        port = u.port or (443 if u.scheme == "wss" else 80)
        path = u.path or "/"
        if u.query:
            path += "?" + u.query

        raw = socket.create_connection((u.hostname, port), timeout=timeout)
        if u.scheme == "wss":
            raw = ssl.create_default_context().wrap_socket(raw, server_hostname=u.hostname)
        self.sock = raw
        self._buf = b""
        self.close_code = None

        key = base64.b64encode(os.urandom(16)).decode()
        lines = [f"GET {path} HTTP/1.1", f"Host: {u.hostname}",
                 "Upgrade: websocket", "Connection: Upgrade",
                 f"Sec-WebSocket-Key: {key}", "Sec-WebSocket-Version: 13"]
        if protocols:
            lines.append("Sec-WebSocket-Protocol: " + ", ".join(protocols))
        self.sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())

        head = self._read_until(b"\r\n\r\n")
        status = head.split(b"\r\n", 1)[0].decode(errors="replace")
        if "101" not in status:
            raise WSError(f"handshake refused: {status}")

    def _read_until(self, marker):
        while marker not in self._buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise WSError("closed during handshake")
            self._buf += chunk
        head, self._buf = self._buf.split(marker, 1)
        return head

    def _read_exactly(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(max(4096, n - len(self._buf)))
            if not chunk:
                raise WSError("connection closed")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _send_frame(self, opcode, payload=b""):
        # Client frames must be masked, even a close.
        header = bytearray([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 1 << 16:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + mask + masked)

    def recv(self, timeout=None):
        """One text message as a str, or None on close/timeout."""
        if timeout is not None:
            self.sock.settimeout(timeout)
        parts, opcode = [], None
        while True:
            try:
                b0, b1 = self._read_exactly(2)
            except (socket.timeout, ssl.SSLWantReadError, TimeoutError):
                return None
            fin, op, length = b0 & 0x80, b0 & 0x0F, b1 & 0x7F
            if b1 & 0x80:
                raise WSError("server frame was masked")
            if length == 126:
                length = struct.unpack(">H", self._read_exactly(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read_exactly(8))[0]
            payload = self._read_exactly(length) if length else b""

            if op == _PING:
                self._send_frame(_PONG, payload)
                continue
            if op == _PONG:
                continue
            if op == _CLOSE:
                self.close_code = struct.unpack(">H", payload[:2])[0] if len(payload) >= 2 else 1005
                self.close()
                return None
            if op in (_TEXT, _BINARY):
                opcode = op
            parts.append(payload)
            if fin:
                return b"".join(parts).decode("utf-8", "replace")

    def close(self):
        try:
            self._send_frame(_CLOSE, struct.pack(">H", 1000))
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


def monitor(api_key, call_id, host="api.retellai.com", timeout=30):
    """Retell's live-call monitor stream.

    The credential rides in the subprotocol list rather than a header, because
    browsers cannot set headers on a WS handshake and the server only accepts
    that form. "bearer" must come first -- the server echoes the first entry
    back, and that must not be the key.
    """
    return WebSocket(f"wss://{host}/v2/monitor-call/{urllib.parse.quote(call_id)}",
                     protocols=["bearer", api_key], timeout=timeout)
