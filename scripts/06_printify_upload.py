import requests, os, json, base64

PRINTIFY_KEY = os.environ['PRINTIFY_API_KEY']
SHOP_ID = os.environ['PRINTIFY_SHOP_ID']
NOTION_TOKEN = os.environ['NOTION_TOKEN']
DB_ID = os.environ['NOTION_DATABASE_ID']

pfy_headers = {'Authorization': f'Bearer {PRINTIFY_KEY}', 'Content-Type': 'application/json'}
notion_headers = {'Authorization': f'Bearer {NOTION_TOKEN}', 'Notion-Version': '2022-06-28'}

# Fetch approved designs from Notion
resp = requests.post(
    f'https://api.notion.com/v1/databases/{DB_ID}/query',
    headers=notion_headers,
    json={'filter': {'property': 'Status', 'select': {'equals': 'Approved'}}}
)
approved = resp.json()['results']

for page in approved:
    props = page['properties']
    title = props['Etsy Title']['rich_text'][0]['text']['content']

    # Get image from Notion page and re-upload to Printify
    # (simplest approach: re-download from stored URL)
    img_url = props.get('Image URL', {}).get('url', '')

    # Upload image to Printify
    img_upload = requests.post(
        f'https://api.printify.com/v1/uploads/images.json',
        headers=pfy_headers,
        json={'file_name': f'{title}.png', 'url': img_url}
    )
    printify_image_id = img_upload.json()['id']

    # Create draft product (Bella+Canvas 3001 unisex tee = blueprint_id 6)
    product = requests.post(
        f'https://api.printify.com/v1/shops/{SHOP_ID}/products.json',
        headers=pfy_headers,
        json={
            'title': title,
            'blueprint_id': 6,  # Bella+Canvas 3001
            'print_provider_id': 99,  # check your printify for best provider
            'variants': [
                {'id': 17887, 'price': 2499, 'is_enabled': True},  # S
                {'id': 17888, 'price': 2499, 'is_enabled': True},  # M
                {'id': 17889, 'price': 2499, 'is_enabled': True},  # L
                {'id': 17890, 'price': 2499, 'is_enabled': True},  # XL
            ],
            'print_areas': [{
                'variant_ids': [17887, 17888, 17889, 17890],
                'placeholders': [{'position': 'front', 'images': [
                    {'id': printify_image_id, 'x': 0.5, 'y': 0.5, 'scale': 0.8, 'angle': 0}
                ]}]
            }]
        }
    ).json()

    draft_url = f"https://printify.com/app/shop/{SHOP_ID}/products/{product['id']}/edit"

    # Update Notion with draft URL
    requests.patch(
        f"https://api.notion.com/v1/pages/{page['id']}",
        headers=notion_headers,
        json={'properties': {
            'Status': {'select': {'name': 'Uploaded'}},
            'Printify Draft URL': {'url': draft_url}
        }}
    )
    print(f"Draft created: {draft_url}")