import requests, json, os
from pathlib import Path
from datetime import datetime
import time
from dotenv import load_dotenv
load_dotenv()

with open('prompts.json') as f:
    prompts = json.load(f)

Path('images').mkdir(exist_ok=True)
results = []

HF_KEY = os.environ['HF_API_KEY']
# API_URL = "https://api-inference.huggingface.co/models/black-forest-labs/FLUX.1-schnell"
API_URL = "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell"
headers = {"Authorization": f"Bearer {HF_KEY}"}

# for i, prompt in enumerate(prompts):
# print(f"\nGenerating image {i+1}/10...")
full_prompt = f"{prompts[1]}, shirt graphic design, bold illustration, no text, white background"

for attempt in range(3):
    print(f"  Attempt {attempt + 1}...")
    response = requests.post(
        API_URL,
        headers=headers,
        json={"inputs": full_prompt},
        timeout=120
    )

    if response.status_code == 200:
        # filename = f"images/design_{i+1:02d}_{datetime.now().strftime('%Y%m%d')}.png"
        filename = f"images/design_{1 + 1:02d}_{datetime.now().strftime('%Y%m%d')}.png"
        with open(filename, 'wb') as f:
            f.write(response.content)
        results.append({
            'prompt': prompts[1],
            'filename': filename,
            'image_url': '',
            'generated_at': datetime.now().isoformat()
        })
        print(f"  Saved {filename}")
        break
    elif response.status_code == 503:
        wait = response.json().get('estimated_time', 20)
        print(f"  Model loading, waiting {wait}s...")
        time.sleep(wait)
    else:
        print(f"  Error {response.status_code}: {response.text}")
        time.sleep(10)

time.sleep(3)

with open('results.json', 'w') as f:
    json.dump(results, f, indent=2)

print(f"\nDone. Generated {len(results)}/10 images.")