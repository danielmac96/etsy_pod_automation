import requests, json, os
from pathlib import Path
from datetime import datetime
import time, urllib.parse
from dotenv import load_dotenv
load_dotenv()
with open('prompts.json') as f:
    prompts = json.load(f)

Path('images').mkdir(exist_ok=True)
results = []

for i, prompt in enumerate(prompts):
    full_prompt = f"{prompt}, transparent background, shirt graphic design, no text, vector style"
    encoded = urllib.parse.quote(full_prompt)

    # Pollinations just needs a GET request — no key needed
    url = f"https://image.pollinations.ai/prompt/{encoded}?width=1024&height=1024&nologo=true&model=flux"

    img_data = requests.get(url, timeout=60).content
    filename = f"images/design_{i + 1:02d}_{datetime.now().strftime('%Y%m%d')}.png"

    with open(filename, 'wb') as f:
        f.write(img_data)

    results.append({
        'prompt': prompt,
        'filename': filename,
        'image_url': url,
        'generated_at': datetime.now().isoformat()
    })

    print(f"Generated {filename}")
    time.sleep(3)  # be polite to their free service

with open('results.json', 'w') as f:
    json.dump(results, f, indent=2)