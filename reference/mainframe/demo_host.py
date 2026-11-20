"""A small synthetic TN3270 host and a scripted 3270 terminal, to try the mainframe recorder
without a mainframe (synthetic data only).

    python reference/mainframe/demo_host.py serve --port 3270      # the host, until Ctrl+C
    python reference/mainframe/demo_host.py flow --port 3270        # a terminal runs the flow

Screens: DEMO SIGN ON (user id, password: non-display), ACCOUNT INQUIRY (account number; Enter
shows the account's name and balance, PF3 signs off), SIGNED OFF. Any user id and password
are accepted except password `WRONG`, which shows a message. Point UiPath's terminal activities
(direct TN3270 connection) or an emulator at the host, or at the recorder's proxy in front of it.
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lab" / "labctl"))

from labctl.workstation.tn3270 import (  # noqa: E402
    AIDS,
    DO,
    EOR,
    IAC,
    SB,
    SE,
    TTYPE,
    WILL,
    Screen,
    Telnet,
    encode_addr,
)

PROT, PROT_HI, INPUT, INPUT_NUM, SECRET = 0x60, 0xE8, 0x40, 0x50, 0x4C
ACCOUNTS = {"1000001": ("ANNA SAMPLE", "1,250.00"), "1000002": ("OMAR EXAMPLE", "80.15")}


def at(row: int, col: int, cols: int = 80) -> int:
    return (row - 1) * cols + (col - 1)


def text(s: str) -> bytes:
    return s.encode("cp037")


def build(items: list[tuple], cursor: tuple[int, int]) -> bytes:
    """An Erase/Write: items are (row, col, attribute, text, length)."""
    out = bytearray([0xF5, 0xC3])  # Erase/Write, WCC: reset, restore keyboard
    for row, col, attr, s, length in items:
        out += bytes([0x11]) + encode_addr(at(row, col)) + bytes([0x1D, attr])
        body = text(s)[:length]
        out += body
        if len(body) < length:  # end the field with a protected stop
            out += bytes([0x11]) + encode_addr(at(row, col) + 1 + length) + bytes([0x1D, PROT])
    out += bytes([0x11]) + encode_addr(at(*cursor)) + bytes([0x13])
    return bytes(out)


def signon(message: str = "") -> bytes:
    return build([(1, 30, PROT_HI, "DEMO SIGN ON", 12), (5, 10, PROT, "USERID . . .", 12),
                  (5, 24, INPUT, "", 8), (6, 10, PROT, "PASSWORD . .", 12), (6, 24, SECRET, "", 8),
                  (22, 2, PROT_HI, message, 60), (24, 2, PROT, "ENTER=SIGN ON  PF3=EXIT", 23)],
                 (5, 25))  # fmt: skip


def inquiry(account: str = "", message: str = "") -> bytes:
    name, balance = ACCOUNTS.get(account, ("", ""))
    if account and not name:
        message = "ACCOUNT NOT FOUND"
    return build([(1, 30, PROT_HI, "ACCOUNT INQUIRY", 15), (4, 10, PROT, "ACCOUNT NO:", 11),
                  (4, 23, INPUT_NUM, account, 10), (6, 10, PROT, "NAME", 4), (6, 23, PROT, name, 30),
                  (7, 10, PROT, "BALANCE", 7), (7, 23, PROT, balance, 15),
                  (22, 2, PROT_HI, message, 60), (24, 2, PROT, "ENTER=SHOW  PF3=SIGN OFF", 24)],
                 (4, 24))  # fmt: skip


def signed_off() -> bytes:
    return build([(1, 30, PROT_HI, "SIGNED OFF", 10)], (1, 1))


def record(data: bytes) -> bytes:
    return data.replace(bytes([IAC]), bytes([IAC, IAC])) + bytes([IAC, EOR])


class _Conn:
    """What Telnet needs of a connection (it notes subnegotiations)."""

    def subnegotiation(self, sub: bytes) -> None:
        pass


def serve_one(sock: socket.socket) -> None:
    sock.sendall(bytes([IAC, DO, TTYPE, IAC, SB, TTYPE, 1, IAC, SE, IAC, DO, EOR, IAC, WILL, EOR,
                        IAC, DO, 0, IAC, WILL, 0]))  # fmt: skip
    telnet = Telnet(_Conn())
    screen = Screen()
    state = "signon"
    sock.sendall(record(signon()))
    screen.write(signon(), (24, 80))
    try:
        while True:
            data = sock.recv(65536)
            if not data:
                return
            for rec in telnet.feed(data):
                got = screen.read(rec)
                if got is None:
                    continue
                aid, _cursor, fields = got
                key = AIDS.get(aid)
                values = {screen.rc(start): value.strip() for start, value in fields}
                if key == "PF3":
                    out = signed_off() if state == "inquiry" else None
                    if out is None:
                        return
                    sock.sendall(record(out))
                    return
                if state == "signon":
                    password = values.get((6, 25), "")
                    if password.upper() == "WRONG":
                        out = signon("SIGN ON FAILED: CHECK THE PASSWORD")
                    else:
                        out, state = inquiry(), "inquiry"
                else:
                    out = inquiry(values.get((4, 24), "").strip())
                screen.write(out, (24, 80))
                sock.sendall(record(out))
    finally:
        sock.close()


def serve(port: int, stop: threading.Event | None = None) -> socket.socket:
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", port))
    server.listen(4)
    server.settimeout(0.5)

    def loop():
        while stop is None or not stop.is_set():
            try:
                client, _ = server.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            threading.Thread(target=serve_one, args=(client,), daemon=True).start()

    threading.Thread(target=loop, daemon=True).start()
    return server


class Terminal:
    """A scripted 3270 terminal: answers the telnet negotiation, keeps the screen, types into
    fields by position and presses keys."""

    def __init__(self, port: int, host: str = "127.0.0.1") -> None:
        self.sock = socket.create_connection((host, port), timeout=10)
        self.screen = Screen()
        self.telnet = Telnet(_Conn())
        self.changed: dict[int, str] = {}
        self._wait_screen()

    def _answer(self, data: bytes) -> None:
        i, out = 0, bytearray()
        while i < len(data) - 1:
            if data[i] == IAC and data[i + 1] in (DO, WILL) and i + 2 < len(data):
                out += bytes([IAC, WILL if data[i + 1] == DO else DO, data[i + 2]])
                i += 3
            elif data[i] == IAC and data[i + 1] == SB and data[i + 2 : i + 4] == bytes([TTYPE, 1]):
                out += bytes([IAC, SB, TTYPE, 0]) + b"IBM-3278-2-E" + bytes([IAC, SE])
                i += 4
            else:
                i += 1
        if out:
            self.sock.sendall(bytes(out))

    def _wait_screen(self) -> None:
        while True:
            data = self.sock.recv(65536)
            if not data:
                raise ConnectionError("the host closed the connection")
            self._answer(data)
            recs = self.telnet.feed(data)
            for rec in recs:
                self.screen.write(rec, (24, 80))
            if recs:
                return

    def type(self, row: int, col: int, value: str) -> None:
        f = self.screen.field_at(at(row, col))
        self.changed[f["start"]] = value

    def text_at(self, row: int, col: int, length: int) -> str:
        return self.screen.text(at(row, col), length)

    def press(self, key: str = "Enter", expect_screen: bool = True) -> None:
        aid = next(b for b, name in {0x7D: "Enter", **{b: n for b, n in AIDS.items()}}.items()
                   if name == key)  # fmt: skip
        out = bytearray([aid]) + encode_addr(self.screen.cursor)
        for start, value in self.changed.items():
            out += bytes([0x11]) + encode_addr(start) + value.encode("cp037")
        self.changed = {}
        self.sock.sendall(record(bytes(out)))
        if expect_screen:
            self._wait_screen()

    def close(self) -> None:
        self.sock.close()


def flow(port: int) -> None:
    """Sign on (a wrong password first), look up two accounts, sign off."""
    t = Terminal(port)
    t.type(5, 25, "TESTER")
    t.type(6, 25, "WRONG")
    t.press("Enter")
    t.type(6, 25, "DEMO123")
    t.press("Enter")
    t.type(4, 24, "1000001")
    t.press("Enter")
    t.type(4, 24, "1000002")
    t.press("Enter")
    t.press("PF3")
    t.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=["serve", "flow"])
    parser.add_argument("--port", type=int, default=3270)
    args = parser.parse_args()
    if args.command == "serve":
        serve(args.port)
        print(f"demo TN3270 host on 127.0.0.1:{args.port} (Ctrl+C ends it)")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
    else:
        flow(args.port)
        print("flow done")
