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

def build_message(report_path: Path) -> EmailMessage:
    email_user = required_env("EMAIL_USER")
    email_to = required_env("EMAIL_TO")
    subject = os.getenv("EMAIL_SUBJECT", "MLB Daily Model Report")

    body_intro = os.getenv(
        "EMAIL_BODY",
        "Your MLB daily model report is attached. If the attachment does not open easily on mobile, the report text is also included below."
    )

    report_text = report_path.read_text(encoding="utf-8")

    msg = EmailMessage()
    msg["From"] = email_user
    msg["To"] = email_to
    msg["Subject"] = subject
    msg.set_content(f"{body_intro}\n\n--- REPORT ---\n\n{report_text}")

    mime_type, _ = mimetypes.guess_type(str(report_path))
    if mime_type:
        maintype, subtype = mime_type.split("/", 1)
    else:
        maintype, subtype = "text", "plain"

    msg.add_attachment(
        report_text.encode("utf-8"),
        maintype=maintype,
        subtype=subtype,
        filename=report_path.name,
    )
    return msg

def send_report(report_path: Path) -> None:
    host = os.getenv("EMAIL_HOST", "smtp.gmail.com")
    port = int(os.getenv("EMAIL_PORT", "465"))
    user = required_env("EMAIL_USER")
    password = required_env("EMAIL_PASS")

    msg = build_message(report_path)

    with smtplib.SMTP_SSL(host, port) as server:
        server.login(user, password)
        server.send_message(msg)

def main() -> None:
    parser = argparse.ArgumentParser(description="Send the MLB report by email.")
    parser.add_argument("report", help="Path to the markdown report file")
    args = parser.parse_args()

    report_path = Path(args.report)
    if not report_path.exists():
        raise FileNotFoundError(f"Report not found: {report_path}")

    send_report(report_path)
    print(f"Email sent successfully: {report_path}")

if __name__ == "__main__":
    main()
