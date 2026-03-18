import smtplib, os, json
from email.mime.text import MIMEText
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()
with open('notion_ids.json') as f:
    ids = json.load(f)

notion_url = f"https://www.notion.so/{os.environ['NOTION_DATABASE_ID'].replace('-', '')}"

body = f"""
Hi! Your weekly shirt designs are ready for review.

{len(ids)} new designs generated on {datetime.now().strftime('%A, %B %d')}.

Review them here: {notion_url}

For each design:
- Change Status from 'Unreviewed' to 'Approved' or 'Rejected'
- Edit the Etsy Title and Tags if needed
- Run the upload script once you're done approving

python scripts/06_printify_upload.py
"""

msg = MIMEText(body)
msg['Subject'] = f"[Etsy Pipeline] {len(ids)} designs ready for review"
msg['From'] = os.environ['GMAIL_USER']
msg['To'] = os.environ['GMAIL_USER']

with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
    smtp.login(os.environ['GMAIL_USER'], os.environ['GMAIL_APP_PASSWORD'])
    smtp.send_message(msg)

print("Notification sent!")