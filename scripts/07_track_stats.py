# import requests, os
#from dotenv import load_dotenv
# load_dotenv()
# ETSY_KEY = os.environ['ETSY_API_KEY']
# SHOP_ID = os.environ['ETSY_SHOP_ID']
# NOTION_TOKEN = os.environ['NOTION_TOKEN']
# DB_ID = os.environ['NOTION_DATABASE_ID']
#
# notion_headers = {'Authorization': f'Bearer {NOTION_TOKEN}', 'Notion-Version': '2022-06-28'}
#
# # Fetch all Notion rows that have an Etsy Post URL
# resp = requests.post(
#     f'https://api.notion.com/v1/databases/{DB_ID}/query',
#     headers=notion_headers,
#     json={'filter': {'property': 'Etsy Post URL', 'url': {'is_not_empty': True}}}
# )
#
# for page in resp.json()['results']:
#     etsy_url = page['properties']['Etsy Post URL']['url']
#     listing_id = etsy_url.split('/')[-1].split('?')[0]  # extract ID from URL
#
#     # Pull listing stats from Etsy API
#     stats = requests.get(
#         f'https://openapi.etsy.com/v3/application/listings/{listing_id}',
#         headers={'x-api-key': ETSY_KEY}
#     ).json()
#
#     views = stats.get('views', 0)
#     favorites = stats.get('num_favorers', 0)
#
#     # Update Notion
#     requests.patch(
#         f"https://api.notion.com/v1/pages/{page['id']}",
#         headers=notion_headers,
#         json={'properties': {
#             'Views': {'number': views},
#             'Favorites': {'number': favorites},
#         }}
#     )
#     print(f"Updated stats for listing {listing_id}: {views} views, {favorites} favs")