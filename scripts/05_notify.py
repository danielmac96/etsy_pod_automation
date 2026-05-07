"""Stage-aware notification email for the weekly Etsy POD pipeline.

Reads `notify_context.json` written by 02/03/04/06. For prompts and images
stages it builds a per-design HTML summary (inline ImgBB thumbnails on
Wednesday) and links to the local Streamlit app at LOCAL_APP_URL.
"""
from __future__ import annotations

import html
import json
import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from dotenv import load_dotenv

load_dotenv()

LOCAL_APP_URL = os.environ.get("LOCAL_APP_URL", "http://localhost:8501")

with open("notify_context.json") as f:
    ctx = json.load(f)

count: int = ctx.get("count", 0)
stage: str = ctx.get("stage", "prompts")
detail: str = ctx.get("detail", "")
items: list[dict] = ctx.get("items", []) or []

date_str = datetime.now().strftime("%A, %B %d")


def _esc(s: str | None) -> str:
    return html.escape(s or "")


def _truncate(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: n - 1] + "…"


def _shell(title: str, app_link: str, intro: str, body_html: str) -> str:
    return f"""<!doctype html>
<html><body style="font-family: -apple-system, Segoe UI, Helvetica, Arial, sans-serif;
                  max-width: 720px; margin: 0 auto; padding: 16px; color: #111;">
  <h2 style="margin:0 0 4px 0">{_esc(title)}</h2>
  <p style="margin:0 0 16px 0; color:#555">{_esc(date_str)}</p>
  <p style="margin:0 0 12px 0">{_esc(intro)}</p>
  <p style="margin:0 0 24px 0">
    <a href="{_esc(app_link)}"
       style="background:#111;color:#fff;padding:10px 16px;border-radius:6px;
              text-decoration:none;font-weight:600">Open local approval app →</a>
  </p>
  {body_html}
  <hr style="border:none;border-top:1px solid #eee;margin:32px 0 8px">
  <p style="color:#999;font-size:12px">
    Local app must be running on this machine. Start with
    <code>streamlit run scripts/approve_app.py</code>.
  </p>
</body></html>"""


def render_prompts() -> tuple[str, str, str]:
    subject = f"[Etsy Pipeline] {count} prompts ready to approve — {date_str}"
    plain = (
        f"{detail}\n\n"
        f"Open the local approval app: {LOCAL_APP_URL}/?tab=prompts\n\n"
        + "\n\n".join(
            f"[{i.get('category','?')}] {i.get('prompt','')[:200]}"
            for i in items
        )
    )
    rows_html = "\n".join(
        f"""<tr>
              <td style="padding:8px;border-bottom:1px solid #eee;vertical-align:top;width:130px;color:#555">
                <strong>{_esc(i.get('category',''))}</strong>
              </td>
              <td style="padding:8px;border-bottom:1px solid #eee;font-size:14px;line-height:1.45">
                {_esc(_truncate(i.get('prompt',''), 320))}
              </td>
            </tr>"""
        for i in items
    )
    body_html = (
        '<table style="width:100%;border-collapse:collapse;font-size:14px">'
        f"{rows_html}"
        "</table>"
    )
    html_doc = _shell(
        title=f"{count} prompts ready to approve",
        app_link=f"{LOCAL_APP_URL}/?tab=prompts",
        intro=detail or f"{count} prompts generated this week.",
        body_html=body_html,
    )
    return subject, plain, html_doc


def render_images() -> tuple[str, str, str]:
    subject = f"[Etsy Pipeline] {count} images ready to approve — {date_str}"
    plain = (
        f"{detail}\n\n"
        f"Open the local approval app: {LOCAL_APP_URL}/?tab=images\n\n"
        + "\n\n".join(
            f"[{i.get('category','?')}] {i.get('prompt','')[:160]}\n  {i.get('image_url','')}"
            for i in items
        )
    )
    cards = []
    for i in items:
        img = i.get("image_url") or ""
        cards.append(
            f"""<td style="vertical-align:top;padding:8px;width:33%">
                  <a href="{_esc(img)}">
                    <img src="{_esc(img)}" alt="" style="width:100%;border-radius:8px;display:block">
                  </a>
                  <div style="font-size:12px;color:#555;margin-top:6px">
                    <strong>{_esc(i.get('category',''))}</strong> ·
                    <code>{_esc((i.get('lineage_id') or '')[:8])}</code>
                  </div>
                  <div style="font-size:13px;line-height:1.4;margin-top:4px">
                    {_esc(_truncate(i.get('prompt',''), 180))}
                  </div>
                </td>"""
        )
    rows_html = ""
    for chunk in [cards[k:k+3] for k in range(0, len(cards), 3)]:
        while len(chunk) < 3:
            chunk.append("<td></td>")
        rows_html += "<tr>" + "".join(chunk) + "</tr>"
    body_html = f'<table style="width:100%;border-collapse:collapse">{rows_html}</table>'
    html_doc = _shell(
        title=f"{count} images ready to approve",
        app_link=f"{LOCAL_APP_URL}/?tab=images",
        intro=detail or f"{count} images generated this week.",
        body_html=body_html,
    )
    return subject, plain, html_doc


def render_drafts() -> tuple[str, str, str]:
    subject = f"[Etsy Pipeline] {count} Printify drafts ready — {date_str}"
    plain = (
        f"{detail}\n\n"
        f"Open the local approval app: {LOCAL_APP_URL}/?tab=drafts\n\n"
        + "\n\n".join(
            f"- {i.get('etsy_title','(no title)')}\n  {i.get('printify_draft_url','')}"
            for i in items
        )
        + "\n\nSunday's stats sync auto-detects new Etsy listings by title."
    )
    rows_html = "\n".join(
        f"""<tr>
              <td style="padding:8px;border-bottom:1px solid #eee;font-size:14px">
                <strong>{_esc(i.get('etsy_title','(no title)'))}</strong>
              </td>
              <td style="padding:8px;border-bottom:1px solid #eee;font-size:14px;text-align:right">
                <a href="{_esc(i.get('printify_draft_url',''))}">Open in Printify ↗</a>
              </td>
            </tr>"""
        for i in items
    )
    body_html = (
        f'<table style="width:100%;border-collapse:collapse">{rows_html}</table>'
        '<p style="color:#666;font-size:13px;margin-top:16px">'
        "Sunday's stats sync auto-detects new Etsy listings by title — paste manually only if you can't wait."
        "</p>"
    )
    html_doc = _shell(
        title=f"{count} Printify drafts ready",
        app_link=f"{LOCAL_APP_URL}/?tab=drafts",
        intro=detail or f"{count} drafts created.",
        body_html=body_html,
    )
    return subject, plain, html_doc


def render_default() -> tuple[str, str, str]:
    subject = f"[Etsy Pipeline] Pipeline update — {date_str}"
    plain = f"{detail}\n\nOpen the local approval app: {LOCAL_APP_URL}"
    body_html = (
        f"<p style=\"font-size:14px;line-height:1.5\">{_esc(detail)}</p>"
    )
    html_doc = _shell(
        title="Pipeline update",
        app_link=LOCAL_APP_URL,
        intro="A pipeline stage has completed.",
        body_html=body_html,
    )
    return subject, plain, html_doc


renderers = {
    "prompts": render_prompts,
    "images":  render_images,
    "drafts":  render_drafts,
}
subject, plain_body, html_body = renderers.get(stage, render_default)()

msg = MIMEMultipart("alternative")
msg["Subject"] = subject
msg["From"] = os.environ["GMAIL_USER"]
msg["To"] = os.environ["GMAIL_USER"]
msg.attach(MIMEText(plain_body, "plain"))
msg.attach(MIMEText(html_body, "html"))

with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
    smtp.login(os.environ["GMAIL_USER"], os.environ["GMAIL_APP_PASSWORD"])
    smtp.send_message(msg)

print(f"Notification sent: {subject}")
