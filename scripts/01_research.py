from pytrends.request import TrendReq
import requests, json, os


def get_trending_keywords():
    pytrends = TrendReq()
    # Check trends for shirt design niches
    pytrends.build_payload(
        ['funny shirts', 'vintage tee', 'cottagecore shirt', 'motivational tee'],
        timeframe='now 7-d'
    )
    related = pytrends.related_queries()

    # Pull top rising queries per seed keyword
    rising = []
    for kw, data in related.items():
        if data['rising'] is not None:
            rising += data['rising']['query'].head(3).tolist()

    # Also hit Etsy's public listing search for recent bestsellers
    etsy_resp = requests.get(
        'https://openapi.etsy.com/v3/application/listings/active',
        params={'keywords': 'funny t-shirt', 'sort_on': 'score', 'limit': 25},
        headers={'x-api-key': os.environ['ETSY_API_KEY']}
    )
    etsy_tags = []
    for listing in etsy_resp.json().get('results', []):
        etsy_tags += listing.get('tags', [])

    # Combine and deduplicate
    all_keywords = list(set(rising + etsy_tags))[:20]
    with open('keywords.json', 'w') as f:
        json.dump(all_keywords, f)
    print(f"Found {len(all_keywords)} keywords")


get_trending_keywords()