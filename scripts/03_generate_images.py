import requests, json, os, base64
from pathlib import Path
from datetime import datetime

with open('prompts.json') as f:
    prompts = json.load(f)

Path('images').mkdir(exist_ok=True)
results = []

for i, prompt in enumerate(prompts):
    resp = requests.post(
        'https://api.ideogram.ai/generate',
        headers={'Api-Key': os.environ['IDEOGRAM_API_KEY']},
        json={
            'image_request': {
                'prompt': prompt + ', transparent background, shirt design, no text',
                'model': 'V_2',
                'aspect_ratio': 'ASPECT_1_1',
                'style_type': 'DESIGN',
                'negative_prompt': 'blurry, photorealistic, photograph'
            }
        }
    )
    data = resp.json()
    image_url = data['data'][0]['url']

    # Download image
    img_data = requests.get(image_url).content
    filename = f"images/design_{i + 1:02d}_{datetime.now().strftime('%Y%m%d')}.png"
    with open(filename, 'wb') as f:
        f.write(img_data)

    results.append({
        'prompt': prompt,
        'filename': filename,
        'image_url': image_url,
        'generated_at': datetime.now().isoformat()
    })
    print(f"Generated {filename}")

with open('results.json', 'w') as f:
    json.dump(results, f, indent=2)