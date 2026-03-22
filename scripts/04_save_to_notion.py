import requests, json, os
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

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

for i, r in enumerate(results):
    page = requests.post(
        'https://api.notion.com/v1/pages',
        headers=headers,
        json={
            'parent': {'database_id': DB_ID},
            'properties': {
                'Title': {
                    'rich_text': [{'text': {'content': f"Design {datetime.now().strftime('%b %d')} #{i + 1}"}}]
                },
                'Prompt': {
                    'title': [{'text': {'content': r['prompt']}}]
                },
                'Status': {
                    'multi_select': [{'name': 'Unreviewed'}]
                },
                'Generated At': {
                    'date': {'start': r['generated_at']}
                },
                'Etsy Title': {
                    'rich_text': [{'text': {'content': 'Auto-generated — edit before posting'}}]
                },
                'Etsy Price': {
                    'number': 24.99
                },
                'Printify Draft URL': {
                    'url': None
                },
                'Etsy Post URL': {
                    'url': None
                },
                'Views': {
                    'number': 0
                },
                'Favorites': {
                    'number': 0
                },
                'Sales': {
                    'number': 0
                },
            }
        }
    ).json()

    print("Notion response:", json.dumps(page, indent=2))

    if 'id' in page:
        notion_page_ids.append(page['id'])
        print(f"Saved to Notion: {page['id']}")
    else:
        print(f"Failed to save design {i + 1}")

with open('notion_ids.json', 'w') as f:
    json.dump(notion_page_ids, f)

print(f"\nDone. Saved {len(notion_page_ids)}/{len(results)} rows to Notion.")