import google.generativeai as genai
import json, os, re, requests
from dotenv import load_dotenv
load_dotenv()

NOTION_TOKEN = os.environ['NOTION_TOKEN']
NOTION_DATABASE_ID = os.environ['NOTION_DATABASE_ID']

notion_headers = {
    'Authorization': f'Bearer {NOTION_TOKEN}',
    'Notion-Version': '2022-06-28',
    'Content-Type': 'application/json'
}

def get_top_performers():
    try:
        resp = requests.post(
            f'https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query',
            headers=notion_headers,
            json={
                'sorts': [{'property': 'Favorites', 'direction': 'descending'}],
                'page_size': 5
            }
        )
        results = resp.json().get('results', [])
        top = [
            p['properties']['Prompt']['rich_text'][0]['text']['content']
            for p in results
            if p['properties'].get('Favorites', {}).get('number', 0) > 0
        ]
        return top
    except Exception as e:
        print(f"No top performers yet (first run): {e}")
        return []

top_prompts = get_top_performers()

genai.configure(api_key=os.environ['GEMINI_API_KEY'])
# model = genai.GenerativeModel('gemini-2.0-flash')
# model = genai.GenerativeModel('gemini-1.5-flash')
model = genai.GenerativeModel('gemini-2.5-flash')

with open('keywords.json') as f:
    keywords = json.load(f)

system = """You are a shirt designer for a brand called Burnout and Barbells. 
The target customer is an athlete who works a corporate 9-5 job — they lift weights, 
run, or train seriously but spend their days in meetings and spreadsheets. 
The tone is self-aware, darkly funny, and relatable. Think meme-worthy but wearable.

Given trending keywords, generate 10 unique image prompts for print-on-demand shirt designs.
Each prompt should:
- Reflect the tension between corporate life and athletic identity
- Be optimized for a bold graphic tee (transparent PNG, works on dark or light shirts)
- Describe the art style clearly (e.g. retro 80s athletic, brutalist bold type, vintage gym poster)
- Avoid rendering text/words in the image itself
- Be 1-2 sentences max

Return a JSON array of exactly 10 strings."""

# Include top performers in prompt if we have any
performer_context = ""
if top_prompts:
    performer_context = f"\n\nTop performing prompts from past weeks (use as style reference): {top_prompts}"

resp = model.generate_content(
    f"{system}{performer_context}\n\nKeywords this week: {', '.join(keywords[:10])}"
)

raw = resp.text
match = re.search(r'\[.*\]', raw, re.DOTALL)
prompts = json.loads(match.group())

with open('prompts.json', 'w') as f:
    json.dump(prompts, f, indent=2)

print("Generated prompts:", prompts)