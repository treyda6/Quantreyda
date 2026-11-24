"""Send yourself an email. Used by alerts.py and weekly.py.

Works with any email account that allows SMTP. For Gmail:
  1. Turn on 2-Step Verification for your Google account.
  2. Create an "App password" (Google Account > Security > App passwords).
  3. Put these in .env (it never leaves your computer):
       SMTP_USER=you@gmail.com
       SMTP_PASSWORD=the 16-character app password
       EMAIL_TO=you@gmail.com            # optional, defaults to SMTP_USER
     SMTP_HOST / SMTP_PORT default to Gmail (smtp.gmail.com, 587).

Without these settings nothing is sent; the caller saves the text to
reports/ instead, so nothing is lost.
"""

import os
import smtplib
import ssl
from email.message import EmailMessage

from schema import load_dotenv


def configured():
    load_dotenv()
    return bool(os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASSWORD"))


def to_html(markdown_text):
    try:
        import markdown
        body = markdown.markdown(markdown_text, extensions=["tables"])
    except ImportError:  # still readable without the package
        from html import escape
        body = f"<pre>{escape(markdown_text)}</pre>"
    return ("<html><body style='font-family:-apple-system,Segoe UI,sans-serif;"
            "max-width:820px;line-height:1.45'>"
            "<style>table{border-collapse:collapse}td,th{border:1px solid #ddd;"
            "padding:4px 8px;font-size:13px}</style>" + body + "</body></html>")


def build_message(subject, markdown_text, sender, to):
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, sender, to
    msg.set_content(markdown_text)
    msg.add_alternative(to_html(markdown_text), subtype="html")
    return msg


def send(subject, markdown_text):
    """True if sent, False if email isn't set up. Raises on a real failure."""
    if not configured():
        return False
    user = os.environ["SMTP_USER"]
    to = os.environ.get("EMAIL_TO") or user
    msg = build_message(subject, markdown_text, user, to)
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))
    with smtplib.SMTP(host, port, timeout=30) as s:
        s.starttls(context=ssl.create_default_context())
        s.login(user, os.environ["SMTP_PASSWORD"])
        s.send_message(msg)
    return True


def selftest():
    m = build_message("Hi", "# Title\n\n| a | b |\n|---|---|\n| 1 | 2 |\n", "x@y.z", "x@y.z")
    html = m.get_body(("html",)).get_content()
    assert "<table>" in html and "<h1>Title</h1>" in html, html
    assert "| a | b |" in m.get_body(("plain",)).get_content()
    print("mailer selftest ok")


if __name__ == "__main__":
    selftest()
