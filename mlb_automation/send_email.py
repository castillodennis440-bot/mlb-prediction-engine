#!/usr/bin/env python3
import argparse
import mimetypes
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def build_message(attachment_path: Path, body_text: str | None = None) -> EmailMessage:
    email_user = required_env("EMAIL_USER")
    email_to = required_env("EMAIL_TO")
    subject = os.getenv("EMAIL_SUBJECT", "MLB Daily Model Report")

    default_body = os.getenv(
        "EMAIL_BODY",
        "Your MLB daily model report is attached as a PDF. A text copy is included below when available."
    )

    msg = EmailMessage()
    msg["From"] = email_user
    msg["To"] = email_to
    msg["Subject"] = subject

    if body_text:
        msg.set_content(f"{default_body}\n\n--- REPORT ---\n\n{body_text}")
    else:
        msg.set_content(default_body)

    mime_type, _ = mimetypes.guess_type(str(attachment_path))
    if mime_type:
        maintype, subtype = mime_type.split("/", 1)
    else:
        maintype, subtype = "application", "octet-stream"

    payload = attachment_path.read_bytes()
    msg.add_attachment(
        payload,
        maintype=maintype,
        subtype=subtype,
        filename=attachment_path.name,
    )
    return msg


def send_report(attachment_path: Path, body_file: Path | None = None) -> None:
    host = os.getenv("EMAIL_HOST", "smtp.gmail.com")
    port = int(os.getenv("EMAIL_PORT", "465"))
    user = required_env("EMAIL_USER")
    password = required_env("EMAIL_PASS")

    body_text = None
    if body_file and body_file.exists():
        body_text = body_file.read_text(encoding="utf-8")

    msg = build_message(attachment_path, body_text=body_text)

    with smtplib.SMTP_SSL(host, port) as server:
        server.login(user, password)
        server.send_message(msg)


def main() -> None:
    parser = argparse.ArgumentParser(description="Send the MLB report by email.")
    parser.add_argument("attachment", help="Path to the file to attach")
    parser.add_argument("--body-file", default=None, help="Optional text/markdown file to include in email body")
    args = parser.parse_args()

    attachment_path = Path(args.attachment)
    if not attachment_path.exists():
        raise FileNotFoundError(f"Attachment not found: {attachment_path}")

    body_file = Path(args.body_file) if args.body_file else None
    send_report(attachment_path, body_file=body_file)
    print(f"Email sent successfully: {attachment_path}")


if __name__ == "__main__":
    main()
