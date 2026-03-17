import requests, json, os, base64
from datetime import datetime

NOTION_TOKEN = os.environ['NOTION_TOKEN']
DB_ID = os.environ['NOTION_DATABASE_ID']
headers = {
    'Authorization': f'Bearer {NOTION_TOKEN}',
    'Content-Type': 'application/json',
    'Notion-Version': '2022-06-28'
}

with open('results.json') as f:
    results = json.load(f)

notion_page_ids = []

for r in results:
    # Upload image file to Notion (via file upload endpoint)
    with open(r['filename'], 'rb') as img:
        upload_resp = requests.post(
            'https://api.notion.com/v1/file-uploads',
            headers={**headers, 'Content-Type': 'multipart/form-data'},
            files={'file': (r['filename'], img, 'image/png')}
        )

    page = requests.post(
        'https://api.notion.com/v1/pages',
        headers=headers,
        json={
            'parent': {'database_id': DB_ID},
            'properties': {
                'Title': {'title': [
                    {'text': {'content': f"Design {datetime.now().strftime('%b %d')} #{results.index(r) + 1}"}}]},
                'Prompt': {'rich_text': [{'text': {'content': r['prompt']}}]},
                'Status': {'select': {'name': 'Unreviewed'}},
                'Generated At': {'date': {'start': r['generated_at']}},
                'Etsy Price': {'number': 24.99},
                'Etsy Title': {'rich_text': [{'text': {'content': 'Auto-generated — edit before posting'}}]},
            }
        }
    ).json()

    notion_page_ids.append(page['id'])
    print(f"Saved to Notion: {page['id']}")

with open('notion_ids.json', 'w') as f:
    json.dump(notion_page_ids, f)