#!/usr/bin/env python3
"""HamachiWrapper - simple PyQt6 GUI wrapper around the hamachi CLI.

Version 0.1.1
Log format: [{timestamp}] [{name}] [{type}]: {command}
"""

import re
import subprocess
import sys
from datetime import datetime

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QClipboard, QFont
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMenu, QPushButton, QTextEdit, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
    QWidget,
)

VERSION = "0.1.1"
APP_NAME = "HamachiWrapper"
HAMACHI_BIN = "hamachi"
SERVICE_NAME = "logmein-hamachi"
REFRESH_MS = 5000
CMD_TIMEOUT = 20

THROBBER_FRAMES = ["_[^,^]| ", "\\[^,^]/", "|[^,^]_", "\\[^,^]/"]

LOG_COLORS = {
    "cmd": "#b388ff",
    "out": "#d0d0d0",
    "ok": "#69f0ae",
    "warn": "#ffd54f",
    "error": "#ff5252",
    "info": "#80d8ff",
}

STYLE = """
QWidget { background-color: #0a0a0f; color: #d8c8ff; font-family: monospace; font-size: 12px; }
QMainWindow { background-color: #0a0a0f; }
QPushButton {
    background-color: #2a1a4a; color: #e8dcff;
    border-top: 2px solid #8a5cff; border-left: 2px solid #8a5cff;
    border-bottom: 2px solid #1a0f30; border-right: 2px solid #1a0f30;
    padding: 4px 10px;
}
QPushButton:pressed {
    border-top: 2px solid #1a0f30; border-left: 2px solid #1a0f30;
    border-bottom: 2px solid #8a5cff; border-right: 2px solid #8a5cff;
}
QPushButton:disabled { color: #6a5a8a; }
QLineEdit, QTextEdit, QTreeWidget {
    background-color: #05050a; color: #e8dcff;
    border-top: 2px solid #1a0f30; border-left: 2px solid #1a0f30;
    border-bottom: 2px solid #8a5cff; border-right: 2px solid #8a5cff;
}
QHeaderView::section { background-color: #2a1a4a; color: #e8dcff; border: 1px solid #8a5cff; padding: 2px; }
QTreeWidget::item:selected { background-color: #6a3fd0; color: #ffffff; }
QMenu { background-color: #1a0f30; border: 1px solid #8a5cff; }
QMenu::item:selected { background-color: #6a3fd0; }
"""


# ---------------------------------------------------------------- parsing

def parse_status(text):
    """Parse bare `hamachi` output into a dict of lowercase keys."""
    info = {}
    for line in text.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            info[key.strip().lower()] = val.strip()
    return info


NET_RE = re.compile(r"^\s*([*x!])\s*\[([^\]]*)\]\s*(.*)$")
PEER_RE = re.compile(r"^\s*([*x!?]?)\s*(\d{1,3}(?:\.\d{1,3}){3})\s+(.*)$")


def parse_list(text):
    """Parse `hamachi list` into [{id, name, online, extra, peers: [...]}]."""
    networks = []
    current = None
    for line in text.splitlines():
        if not line.strip():
            continue
        m = NET_RE.match(line)
        if m:
            flag, net_id, rest = m.groups()
            name = re.split(r"\s{2,}|\s+capacity:", rest.strip())[0].strip()
            current = {
                "id": net_id.strip(),
                "name": name or net_id.strip(),
                "online": flag == "*",
                "extra": rest.strip(),
                "peers": [],
            }
            networks.append(current)
            continue
        m = PEER_RE.match(line)
        if m and current is not None:
            flag, ip, rest = m.groups()
            parts = rest.split()
            nick = parts[0] if parts else ""
            am = re.search(r"alias:\s*(\S+)", rest)
            alias = am.group(1) if am else ""
            rm = re.search(r"(\d{1,3}(?:\.\d{1,3}){3}:\d+|\[[0-9a-fA-F:]+\]:\d+)", rest)
            remote = rm.group(1) if rm else ""
            pm = re.search(r"\b(UDP|TCP)\b", rest)
            proto = pm.group(1) if pm else ""
            ctype = "relay" if "relay" in rest.lower() else ("direct" if "direct" in rest.lower() else "")
            conn = " ".join(p for p in parts[1:] if not p.startswith("alias:"))
            current["peers"].append({
                "ip": ip,
                "nick": nick,
                "online": flag == "*",
                "conn": conn,
                "alias": alias,
                "remote": remote,
                "proto": proto,
                "ctype": ctype,
                "raw": line.rstrip(),
                "net": current["name"],
            })
    return networks


# ---------------------------------------------------------------- workers

class WorkerSignals(QObject):
    done = pyqtSignal(object)


class CmdWorker(QRunnable):
    """Runs a command list in a thread and emits a result dict."""

    def __init__(self, args, tag, silent=False):
        super().__init__()
        self.args = args
        self.tag = tag
        self.silent = silent
        self.signals = WorkerSignals()
        self.setAutoDelete(False)
        self.finished = False

    def run(self):
        result = {"args": self.args, "tag": self.tag, "silent": self.silent,
                  "rc": -1, "out": "", "missing": False}
        try:
            p = subprocess.run(self.args, capture_output=True, text=True,
                               timeout=CMD_TIMEOUT)
            result["rc"] = p.returncode
            result["out"] = (p.stdout + p.stderr).strip()
        except FileNotFoundError:
            result["missing"] = True
            result["out"] = f"{self.args[0]} not found"
        except subprocess.TimeoutExpired:
            result["out"] = f"timed out after {CMD_TIMEOUT}s"
        except Exception as e:  # noqa: BLE001
            result["out"] = str(e)
        self.finished = True
        self.signals.done.emit(result)


# ---------------------------------------------------------------- window

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {VERSION}")
        self.resize(760, 640)
        self.pool = QThreadPool.globalInstance()
        self.workers = []
        self.pending = 0
        self.refresh_busy = False
        self.frame = 0

        root = QWidget()
        self.setCentralWidget(root)
        v = QVBoxLayout(root)

        # status row
        self.status_label = QLabel("Status: <unknown>")
        self.info_label = QLabel("")
        self.throbber = QLabel("")
        self.throbber.setFont(QFont("monospace", 12))
        top = QHBoxLayout()
        top.addWidget(self.status_label)
        top.addWidget(self.info_label, 1)
        top.addWidget(self.throbber)
        v.addLayout(top)

        # login row
        row1 = QHBoxLayout()
        self.btn_login = QPushButton("Login")
        self.btn_logout = QPushButton("Logout")
        self.btn_refresh = QPushButton("Refresh")
        self.btn_daemon = QPushButton("Start Daemon")
        self.chk_throb = QCheckBox("Throbber")
        self.chk_throb.setChecked(True)
        for b in (self.btn_login, self.btn_logout, self.btn_refresh, self.btn_daemon):
            row1.addWidget(b)
        row1.addStretch(1)
        row1.addWidget(self.chk_throb)
        v.addLayout(row1)

        # tree
        self.tree = QTreeWidget()
        self.tree.setColumnCount(8)
        self.tree.setHeaderLabels(["Network / Peer", "ID / IP", "Online", "Type",
                                   "Proto", "Remote Address", "Alias", "Raw"])
        for i, w in enumerate((200, 120, 55, 60, 50, 170, 100, 400)):
            self.tree.setColumnWidth(i, w)
        self.tree.header().setStretchLastSection(True)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        v.addWidget(self.tree, 3)

        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setPlaceholderText("select a network or peer to see all of its data")
        self.detail.setMaximumHeight(130)
        v.addWidget(self.detail, 1)

        # join row
        row2 = QHBoxLayout()
        self.in_id = QLineEdit()
        self.in_id.setPlaceholderText("network id")
        self.in_pw = QLineEdit()
        self.in_pw.setPlaceholderText("password [optional]")
        self.in_pw.setEchoMode(QLineEdit.EchoMode.Password)
        row2.addWidget(QLabel("Network ID:"))
        row2.addWidget(self.in_id, 2)
        row2.addWidget(QLabel("Pass:"))
        row2.addWidget(self.in_pw, 1)
        v.addLayout(row2)

        row3 = QHBoxLayout()
        self.btn_join = QPushButton("Join")
        self.btn_leave = QPushButton("Leave")
        self.btn_create = QPushButton("Create")
        self.btn_on = QPushButton("Go Online")
        self.btn_off = QPushButton("Go Offline")
        self.btn_raw = QPushButton("Raw List")
        self.btn_peer = QPushButton("Peer Info")
        for b in (self.btn_join, self.btn_leave, self.btn_create, self.btn_on, self.btn_off,
                  self.btn_raw, self.btn_peer):
            row3.addWidget(b)
        row3.addStretch(1)
        v.addLayout(row3)

        # log
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        v.addWidget(self.log, 2)

        # signals
        self.btn_login.clicked.connect(lambda: self.run_hamachi(["login"], "login"))
        self.btn_logout.clicked.connect(lambda: self.run_hamachi(["logout"], "logout"))
        self.btn_refresh.clicked.connect(lambda: self.refresh(silent=False))
        self.btn_daemon.clicked.connect(self.start_daemon)
        self.btn_join.clicked.connect(self.do_join)
        self.btn_leave.clicked.connect(self.do_leave)
        self.btn_create.clicked.connect(self.do_create)
        self.btn_on.clicked.connect(lambda: self.net_cmd("go-online"))
        self.btn_off.clicked.connect(lambda: self.net_cmd("go-offline"))
        self.btn_raw.clicked.connect(lambda: self.run_hamachi(["list"], "rawlist"))
        self.btn_peer.clicked.connect(self.do_peer_info)
        self.tree.customContextMenuRequested.connect(self.tree_menu)
        self.tree.itemSelectionChanged.connect(self.on_select)

        # timers
        self.timer = QTimer(self)
        self.timer.timeout.connect(lambda: self.refresh(silent=True))
        self.timer.start(REFRESH_MS)
        self.anim = QTimer(self)
        self.anim.timeout.connect(self.tick_throbber)
        self.anim.start(200)

        self.logmsg("info", f"{APP_NAME} {VERSION} started")
        self.refresh(silent=False)

    # ---- logging
    def logmsg(self, kind, text):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        color = LOG_COLORS.get(kind, "#d0d0d0")
        safe = (text.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace("\n", "<br>"))
        self.log.append(
            f'<span style="color:{color}">[{ts}] [{APP_NAME}] [{kind}]: {safe}</span>')

    # ---- throbber
    def tick_throbber(self):
        if self.pending > 0 and self.chk_throb.isChecked():
            self.throbber.setText(THROBBER_FRAMES[self.frame % 4])
            self.frame += 1
        else:
            self.throbber.setText("")

    # ---- running commands
    def spawn(self, args, tag, silent=False):
        w = CmdWorker(args, tag, silent)
        w.signals.done.connect(self.on_done)
        self.pending += 1
        self.workers.append(w)
        if not silent:
            self.logmsg("cmd", " ".join(args))
        self.pool.start(w)

    def run_hamachi(self, args, tag, silent=False):
        self.spawn([HAMACHI_BIN] + args, tag, silent)

    def refresh(self, silent=True):
        if self.refresh_busy:
            return
        self.refresh_busy = True
        self.run_hamachi([], "status", silent)

    def on_done(self, r):
        self.pending = max(0, self.pending - 1)
        self.workers = [w for w in self.workers if not w.finished]
        tag, out, rc, silent = r["tag"], r["out"], r["rc"], r["silent"]

        if tag == "status":
            self.handle_status(r)
            return
        if tag == "list":
            self.refresh_busy = False
            if rc == 0:
                self.fill_tree(parse_list(out))
            elif not silent:
                self.logmsg("error", out or f"rc={rc}")
            return
        if tag in ("rawlist", "peerinfo"):
            self.detail.setPlainText(out or f"(no output, rc={rc})")
            self.logmsg("out" if rc == 0 else "error",
                        f"{tag}: rc={rc}, {len(out.splitlines())} lines shown in detail pane")
            return
        if tag == "daemon":
            self.logmsg("ok" if rc == 0 else "error", out or f"rc={rc}")
            self.refresh(silent=False)
            return

        if out:
            self.logmsg("out" if rc == 0 else "error", out)
        if rc != 0 and not out:
            self.logmsg("error", f"rc={rc}")
        self.refresh(silent=True)

    def handle_status(self, r):
        out, rc, silent = r["out"], r["rc"], r["silent"]
        if r["missing"]:
            self.refresh_busy = False
            self.status_label.setText("Status: hamachi not installed")
            self.info_label.setText("yay -S logmein-hamachi")
            if not silent:
                self.logmsg("error", "hamachi binary not found in PATH")
            return
        low = out.lower()
        if "do not have permission" in low:
            self.refresh_busy = False
            self.status_label.setText("Status: no permission for hamachid")
            self.info_label.setText("see log for the fix")
            if not silent or not getattr(self, "perm_warned", False):
                self.perm_warned = True
                self.logmsg("error", out)
                self.logmsg("warn", "fix: add 'Ipc.User <login name>' to "
                            "/var/lib/logmein-hamachi/h2-engine-override.cfg, "
                            "then: sudo systemctl restart logmein-hamachi")
            return
        if "unable to connect" in low or "not running" in low or "daemon" in low and rc != 0:
            self.refresh_busy = False
            self.status_label.setText("Status: daemon not running")
            self.info_label.setText("")
            if not silent:
                self.logmsg("error", out or "daemon not running")
                self.logmsg("warn", "click [ Start Daemon ]")
            return
        info = parse_status(out)
        status = info.get("status", "unknown")
        nick = info.get("nickname", "")
        addr = info.get("address", "").split()
        addr = addr[0] if addr else ""
        self.status_label.setText(f"Status: {status}")
        self.info_label.setText(f"{nick}  {addr}".strip())
        if not silent:
            self.logmsg("out", out)
        if "logged in" in status.lower() or "online" in status.lower():
            self.run_hamachi(["list"], "list", silent)
        else:
            self.refresh_busy = False
            self.tree.clear()

    # ---- tree
    def fill_tree(self, networks):
        expanded = set()
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            if it.isExpanded():
                expanded.add(it.text(1))
        self.tree.clear()
        for n in networks:
            top = QTreeWidgetItem([n["name"], n["id"],
                                   "yes" if n["online"] else "no", "", "", "", "",
                                   n["extra"]])
            top.setData(0, Qt.ItemDataRole.UserRole, n)
            self.tree.addTopLevelItem(top)
            for p in n["peers"]:
                child = QTreeWidgetItem([p["nick"], p["ip"],
                                         "yes" if p["online"] else "no",
                                         p["ctype"], p["proto"], p["remote"],
                                         p["alias"], p["raw"].strip()])
                child.setData(0, Qt.ItemDataRole.UserRole, p)
                top.addChild(child)
            top.setExpanded(True)

    def on_select(self):
        items = self.tree.selectedItems()
        if not items:
            return
        it = items[0]
        data = it.data(0, Qt.ItemDataRole.UserRole)
        if it.parent() is None:
            self.in_id.setText(it.text(1))
        if isinstance(data, dict):
            lines = []
            for k, val in data.items():
                if k == "peers":
                    val = f"{len(val)} peer(s)"
                lines.append(f"{k:8}: {val}")
            self.detail.setPlainText("\n".join(lines))

    def do_peer_info(self):
        items = self.tree.selectedItems()
        if not items or items[0].parent() is None:
            self.logmsg("warn", "select a peer first")
            return
        self.run_hamachi(["peer", items[0].text(1)], "peerinfo")

    def tree_menu(self, pos):
        item = self.tree.itemAt(pos)
        if not item:
            return
        menu = QMenu(self)
        a_copy = menu.addAction("Copy IP" if item.parent() else "Copy network ID")
        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen is a_copy:
            QApplication.clipboard().setText(item.text(1))
            self.logmsg("info", f"copied {item.text(1)}")

    # ---- actions
    def do_join(self):
        nid = self.in_id.text().strip()
        if not nid:
            self.logmsg("warn", "enter a network id first")
            return
        args = ["join", nid]
        if self.in_pw.text():
            args.append(self.in_pw.text())
        self.run_hamachi_masked(args)

    def do_create(self):
        nid = self.in_id.text().strip()
        if not nid:
            self.logmsg("warn", "enter a network id first")
            return
        args = ["create", nid]
        if self.in_pw.text():
            args.append(self.in_pw.text())
        self.run_hamachi_masked(args)

    def run_hamachi_masked(self, args):
        """Run a command but keep the password out of the log."""
        shown = args[:2] + (["****"] if len(args) > 2 else [])
        w = CmdWorker([HAMACHI_BIN] + args, args[0])
        w.signals.done.connect(self.on_done)
        self.pending += 1
        self.workers.append(w)
        self.logmsg("cmd", " ".join([HAMACHI_BIN] + shown))
        self.pool.start(w)

    def do_leave(self):
        self.net_cmd("leave")

    def net_cmd(self, verb):
        nid = self.in_id.text().strip()
        if not nid:
            self.logmsg("warn", "enter or select a network id first")
            return
        self.run_hamachi([verb, nid], verb)

    def start_daemon(self):
        self.spawn(["pkexec", "systemctl", "start", SERVICE_NAME], "daemon")


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
