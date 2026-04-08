import json, random
import google.generativeai as genai
from dotenv import load_dotenv
import os

load_dotenv()
genai.configure(api_key=os.environ["GEMINI_API_KEY"])

SEED_KEYWORDS = [
    "gym before work grind",
    "corporate burnout weightlifting",
    "deadlift then spreadsheets",
    "office worker gains",
    "9 to 5 then 5 to 9 gym",
    "athlete desk job struggle",
    "barbell therapy",
    "PR on friday meeting on monday",
    "powerlifter in a suit",
    "corporate drone lifts heavy",
    "hustle culture gym rat",
    "excel by day squats by night",
    "burnout and barbells",
    "salary slave with six pack",
    "meetings and macros",
    "linkedin in the streets",
    "rest day is not a thing",
    "caffeine and creatine",
    "work hard lift harder",
    "quarterly review PR attempt"
]

def generate_keywords(seeds: list[str], n: int = 20) -> list[str]:
    model = genai.GenerativeModel("gemini-2.5-flash")

    prompt = f"""You are a creative director for a gym/corporate culture apparel brand.

    Here are example keywords that capture the brand voice:
    {json.dumps(random.sample(seeds, 8), indent=2)}
    
    Generate {n} NEW keyword phrases in the exact same style:
    - Short (2-5 words)
    - Captures the tension between office/corporate life and gym/lifting culture
    - Sardonic, relatable, meme-aware tone
    - No repeats of the examples above
    
    Return ONLY a JSON array of strings, no explanation."""

    response = model.generate_content(prompt)
    text = response.text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(text)


keywords = generate_keywords(SEED_KEYWORDS, n=20)

with open('keywords.json', 'w') as f:
    json.dump(keywords, f, indent=2)

print(f"Keywords this week: {keywords}")