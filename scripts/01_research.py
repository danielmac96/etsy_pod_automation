import requests, json, random
from xml.etree import ElementTree
from dotenv import load_dotenv

load_dotenv()

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


def get_seasonal_themes():
    """Pull trending RSS but only keep non-person, non-sports themes"""
    try:
        url = "https://trends.google.com/trending/rss?geo=US"
        resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        root = ElementTree.fromstring(resp.content)

        # Keywords that signal a topic is too news/person/sports specific
        skip_words = [
            'vs', 'game', 'score', 'nfl', 'nba', 'mlb', 'nhl', 'ncaa',
            'election', 'president', 'senator', 'died', 'death', 'killed',
            'arrested', 'trial', 'verdict', 'stock', 'price'
        ]

        themes = []
        for item in root.findall('.//item'):
            title = item.find('title')
            if title is not None:
                text = title.text.lower()
                # Skip if it looks like news/sports/politics
                if not any(skip in text for skip in skip_words):
                    # Only keep if it could inspire a design theme
                    if len(text.split()) <= 3:  # short phrases only
                        themes.append(title.text)

        return themes[:5]
    except Exception as e:
        print(f"RSS fetch failed: {e}, using seeds only")
        return []


seasonal = get_seasonal_themes()
print(f"Seasonal themes kept: {seasonal}")

# Always use mostly seeds (reliable) + a few seasonal if any survived filtering
weekly_keywords = random.sample(SEED_KEYWORDS, 8) #+ seasonal[:2]
weekly_keywords = weekly_keywords[:3]  # cap at 10

with open('keywords.json', 'w') as f:
    json.dump(weekly_keywords, f, indent=2)

print(f"Keywords this week: {weekly_keywords}")