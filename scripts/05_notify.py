import json
import os
import smtplib
from datetime import datetime
from email.mime.text import MIMEText

from dotenv import load_dotenv

load_dotenv()

with open("notify_context.json") as f:
    ctx = json.load(f)

count = ctx.get("count", 0)
stage = ctx.get("stage", "prompts")
detail = ctx.get("detail", "")

notion_url = f"https://www.notion.so/{os.environ['NOTION_DATABASE_ID'].replace('-', '')}"
date_str = datetime.now().strftime("%A, %B %d")

if stage == "prompts":
    subject = f"[Etsy Pipeline] {count} prompts ready to approve — {date_str}"
    body = f"""Hi! This week's design prompts are ready for your review.

{detail}

Review and approve them here: {notion_url}

For each prompt:
- Set Pipeline Status to "Prompt Approved" to queue for image generation
- Set Pipeline Status to "Prompt Rejected" to skip it

Images will be generated automatically on Wednesday for all approved prompts.
"""

elif stage == "images":
    subject = f"[Etsy Pipeline] {count} images ready to approve — {date_str}"
    body = f"""Hi! This week's generated images are ready for your review.

{detail}

Review and approve them here: {notion_url}

For each image:
- Set Pipeline Status to "Image Approved" to generate product copy and create a Printify draft
- Set Pipeline Status to "Image Rejected" to skip it

Product copy and Printify drafts will be created automatically on Thursday for all approved images.
"""

elif stage == "drafts":
    subject = f"[Etsy Pipeline] {count} Printify drafts ready — {date_str}"
    body = f"""Hi! Printify drafts have been created for this week's approved designs.

{detail}

Next steps for each draft:
1. Open the draft link in Printify (saved in the Notion row)
2. Review the design, add mockups, confirm shipping
3. Publish to Etsy
4. Paste the live Etsy listing URL into the Notion row and set status to Published

Track all designs here: {notion_url}
"""

else:
    subject = f"[Etsy Pipeline] Pipeline update — {date_str}"
    body = f"""Hi! A pipeline stage has completed.

{detail}

Review here: {notion_url}
"""

msg = MIMEText(body)
msg["Subject"] = subject
msg["From"] = os.environ["GMAIL_USER"]
msg["To"] = os.environ["GMAIL_USER"]

with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
    smtp.login(os.environ["GMAIL_USER"], os.environ["GMAIL_APP_PASSWORD"])
    smtp.send_message(msg)

print(f"Notification sent: {subject}")
