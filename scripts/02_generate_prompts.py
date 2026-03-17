import google.generativeai as genai
import json, os

# At the top of generate_prompts.py, add:
def get_top_performers():
    resp = requests.post(
        f'https://api.notion.com/v1/databases/{DB_ID}/query',
        headers=notion_headers,
        json={
            'sorts': [{'property': 'Favorites', 'direction': 'descending'}],
            'page_size': 5
        }
    )
    return [p['properties']['Prompt']['rich_text'][0]['text']['content']
            for p in resp.json()['results']
            if p['properties'].get('Favorites', {}).get('number', 0) > 0]

top_prompts = get_top_performers()
# Pass these into your Gemini prompt:
# "Here are top-performing prompts from past weeks: {top_prompts}.
#  Generate 10 new prompts in a similar style using new keywords."

genai.configure(api_key=os.environ['GEMINI_API_KEY'])
model = genai.GenerativeModel('gemini-2.0-flash')

with open('keywords.json') as f:
    keywords = json.load(f)

system = """You are a shirt designer. Given trending keywords, generate 10 unique Midjourney/Ideogram-style image prompts for print-on-demand shirt designs.
Each prompt should:
- Be optimized for a transparent PNG with a white/light shirt in mind
- Describe the art style (e.g. retro vintage, line art, bold graphic)
- Be 1-2 sentences max
- Avoid text/words in the image (hard to render well)
Return a JSON array of exactly 10 strings."""

resp = model.generate_content(
    f"{system}\n\nKeywords this week: {', '.join(keywords[:10])}"
)

# Parse the JSON out of the response
import re
raw = resp.text
match = re.search(r'\[.*\]', raw, re.DOTALL)
prompts = json.loads(match.group())

with open('prompts.json', 'w') as f:
    json.dump(prompts, f, indent=2)

print("Generated prompts:", prompts)