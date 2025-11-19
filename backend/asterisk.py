"""Minimal Asterisk AMI client for Originate action.

Note: For production, prefer a tested library or ARI. This is a simple, synchronous implementation.
"""
from __future__ import annotations
import socket
from typing import Optional, Dict
from .config import env_str

HOST = env_str("ASTERISK_HOST", "127.0.0.1")
PORT = int(env_str("ASTERISK_AMI_PORT", "5038"))
USER = env_str("ASTERISK_AMI_USER")
SECRET = env_str("ASTERISK_AMI_PASSWORD")


def _send(sock: socket.socket, msg: str):
    sock.sendall(msg.encode("utf-8"))


def originate(channel: str, exten: str, context: str = "default", priority: int = 1, callerid: Optional[str] = None, variables: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    if not USER or not SECRET:
        return {"ok": "false", "error": "AMI credentials missing"}
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5)
    s.connect((HOST, PORT))
    _send(s, f"Action: Login\r\nUsername: {USER}\r\nSecret: {SECRET}\r\nEvents: off\r\n\r\n")
    var_lines = ""
    if variables:
        for k, v in variables.items():
            var_lines += f"Variable: {k}={v}\r\n"
    _send(
        s,
        (
            "Action: Originate\r\n"
            f"Channel: {channel}\r\n"
            f"Exten: {exten}\r\n"
            f"Context: {context}\r\n"
            f"Priority: {priority}\r\n"
            + (f"CallerID: {callerid}\r\n" if callerid else "")
            + var_lines
            + "Async: true\r\n\r\n"
        ),
    )
    _send(s, "Action: Logoff\r\n\r\n")
    s.close()
    return {"ok": "true"}
