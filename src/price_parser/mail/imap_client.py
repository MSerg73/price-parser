from __future__ import annotations

import imaplib
import ssl
from dataclasses import dataclass


@dataclass(frozen=True)
class IMAPConfig:
    host: str
    port: int
    username: str
    password: str
    mailbox: str = "INBOX"
    use_ssl: bool = True
    timeout_seconds: float = 30.0


class IMAPMailbox:
    """Thin live adapter. It never deletes mail and fetches with BODY.PEEK[]."""

    is_live = True

    def __init__(self, config: IMAPConfig) -> None:
        self.config = config

    def fetch_unseen(self, *, limit: int = 100) -> list[bytes]:
        cfg = self.config
        if cfg.use_ssl:
            client = imaplib.IMAP4_SSL(
                cfg.host,
                cfg.port,
                ssl_context=ssl.create_default_context(),
                timeout=cfg.timeout_seconds,
            )
        else:
            client = imaplib.IMAP4(cfg.host, cfg.port, timeout=cfg.timeout_seconds)
        try:
            client.login(cfg.username, cfg.password)
            status, _ = client.select(cfg.mailbox, readonly=True)
            if status != "OK":
                raise RuntimeError(f"Cannot select mailbox {cfg.mailbox}")
            status, data = client.uid("search", None, "UNSEEN")
            if status != "OK":
                raise RuntimeError("IMAP search failed")
            uids = (data[0] or b"").split()[-limit:]
            messages: list[bytes] = []
            for uid in uids:
                status, parts = client.uid("fetch", uid, "(BODY.PEEK[])")
                if status != "OK":
                    raise RuntimeError(f"IMAP fetch failed for UID {uid!r}")
                raw = next(
                    (part[1] for part in parts if isinstance(part, tuple) and isinstance(part[1], bytes)),
                    None,
                )
                if raw is not None:
                    messages.append(raw)
            return messages
        finally:
            try:
                client.logout()
            except Exception:
                pass
