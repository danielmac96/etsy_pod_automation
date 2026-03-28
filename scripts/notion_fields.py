"""
Shared Notion database property names (case-sensitive — match your Notion DB exactly).

Create a Notion database with these columns:

  Name                      Title
  Prompt                    Rich text
  Pipeline Status           Select: Unreviewed, Approved, Rejected, Drafted, Published
  Etsy Title                Rich text
  Tags                      Rich text
  Image URL                 URL
  Generated At              Date
  Printify Draft URL        URL
  Etsy Listing URL          URL
  Views                     Number
  Favorites                 Number
  Views Since Last Sync     Number
  Favorites Since Last Sync Number
  Stats Updated             Date
"""

NOTION_VERSION = "2022-06-28"

NAME = "Name"
PROMPT = "Prompt"
PIPELINE_STATUS = "Pipeline Status"
ETSY_TITLE = "Etsy Title"
TAGS = "Tags"
IMAGE_URL = "Image URL"
GENERATED_AT = "Generated At"
PRINTIFY_DRAFT_URL = "Printify Draft URL"
ETSY_LISTING_URL = "Etsy Listing URL"
VIEWS = "Views"
FAVORITES = "Favorites"
VIEWS_SINCE_SYNC = "Views Since Last Sync"
FAVORITES_SINCE_SYNC = "Favorites Since Last Sync"
STATS_UPDATED = "Stats Updated"

STATUS_UNREVIEWED = "Unreviewed"
STATUS_APPROVED = "Approved"
STATUS_REJECTED = "Rejected"
STATUS_DRAFTED = "Drafted"
STATUS_PUBLISHED = "Published"


def notion_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def rich_text_plain(prop: dict) -> str:
    blocks = prop.get("rich_text") or []
    if not blocks:
        return ""
    return blocks[0].get("text", {}).get("content", "") or ""


def title_plain(prop: dict) -> str:
    blocks = prop.get("title") or []
    if not blocks:
        return ""
    return blocks[0].get("text", {}).get("content", "") or ""


def url_value(prop: dict) -> str | None:
    u = prop.get("url")
    return u if u else None


def number_value(prop: dict) -> float | None:
    n = prop.get("number")
    return n if n is not None else None
