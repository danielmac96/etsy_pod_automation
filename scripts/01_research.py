import json
import random

import google.genai as genai
import requests
from dotenv import load_dotenv
import os

load_dotenv()

genai.configure(api_key=os.environ["GEMINI_API_KEY"])
ETSY_API_KEY = os.environ.get("ETSY_API_KEY", "")

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

# Seed queries for Etsy suggested search — real marketplace terms with volume
ETSY_SEED_QUERIES = [
    "gym shirt funny",
    "weightlifting tee",
    "office humor shirt",
    "powerlifter gift",
    "workout motivation shirt",
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


def fetch_etsy_suggestions(query: str) -> list[str]:
    """Fetch real marketplace search suggestions from Etsy API."""
    if not ETSY_API_KEY:
        return []
    try:
        resp = requests.get(
            "https://openapi.etsy.com/v3/application/suggested-searches",
            headers={"x-api-key": ETSY_API_KEY},
            params={"q": query, "limit": 10},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return [entry.get("query", "") for entry in data.get("results", []) if entry.get("query")]
    except Exception as e:
        print(f"Etsy suggestions failed for '{query}': {e}")
        return []


# Gemini-generated keywords
gemini_keywords = generate_keywords(SEED_KEYWORDS, n=20)
annotated = [{"keyword": kw, "source": "gemini"} for kw in gemini_keywords]

# Etsy marketplace suggestions (real search volume signals)
etsy_terms: list[str] = []
for q in ETSY_SEED_QUERIES:
    etsy_terms.extend(fetch_etsy_suggestions(q))

seen = set(kw.lower() for kw in gemini_keywords)
for term in etsy_terms:
    if term and term.lower() not in seen:
        annotated.append({"keyword": term, "source": "etsy"})
        seen.add(term.lower())

with open("keywords.json", "w") as f:
    json.dump(annotated, f, indent=2)

gemini_count = sum(1 for a in annotated if a["source"] == "gemini")
etsy_count = sum(1 for a in annotated if a["source"] == "etsy")
print(f"Keywords this week: {len(annotated)} total ({gemini_count} gemini + {etsy_count} etsy)")
print([a["keyword"] for a in annotated])
