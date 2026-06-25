from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol


class MailTransport(Protocol):
    is_live: bool

    def send(self, message: EmailMessage) -> str:
        ...


@dataclass(frozen=True)
class SMTPConfig:
    host: str
    port: int
    username: str
    password: str
    use_ssl: bool = True
    timeout_seconds: float = 30.0


class SMTPTransport:
    is_live = True

    def __init__(self, config: SMTPConfig) -> None:
        self.config = config

    def send(self, message: EmailMessage) -> str:
        cfg = self.config
        if cfg.use_ssl:
            client = smtplib.SMTP_SSL(
                cfg.host,
                cfg.port,
                timeout=cfg.timeout_seconds,
                context=ssl.create_default_context(),
            )
        else:
            client = smtplib.SMTP(cfg.host, cfg.port, timeout=cfg.timeout_seconds)
        try:
            if not cfg.use_ssl:
                client.starttls(context=ssl.create_default_context())
            client.login(cfg.username, cfg.password)
            refused = client.send_message(message)
            if refused:
                raise RuntimeError(f"SMTP rejected recipients: {sorted(refused)}")
        finally:
            try:
                client.quit()
            except Exception:
                client.close()
        return str(message["Message-ID"])


class MockSMTPTransport:
    is_live = False

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.messages: list[EmailMessage] = []

    def send(self, message: EmailMessage) -> str:
        if self.fail:
            raise RuntimeError("mock SMTP failure")
        self.messages.append(message)
        return str(message["Message-ID"])
