# Etsy v3 API — endpoints we use

Source: Etsy Open API v3 (`https://openapi.etsy.com/v3/application`).
The Etsy MCP server (`mcp.api.etsycloud.com/mcp`) was not available when this
was written, so the contract below is captured from the v3 reference and the
behavior of the prior production client. Validate against the MCP server when
it becomes available and update this file if any field name or shape differs.

## Auth

All read endpoints listed here accept `x-api-key: <ETSY_API_KEY>` only.
No OAuth bearer is required for searching public listings.

## Rate limits

Etsy publishes per-app limits in the Developer Portal rather than a fixed value
in the docs. The defaults Etsy commonly grants new apps:

- 10 requests per second (per app)
- 10,000 requests per day (per app)

Our client uses the more conservative **5 rps / 5,000 per day** ceiling so a
runaway loop or a parallel run on GitHub Actions can't burn the daily budget.
On 429 the response includes a `retry-after` header (seconds). The client
honors it, with fallback to exponential backoff for 5xx and missing headers.

## Endpoints

### `GET /listings/active`

Search active listings (the v3 successor to v2 `findAllListingsActive`).

- **URL:** `https://openapi.etsy.com/v3/application/listings/active`
- **Auth:** `x-api-key` header only
- **Params we send:**
  - `keywords` (string, required for relevance ranking)
  - `limit` (int, 1–100; default 25; we use 50 in research, 5 in dry-run)
  - `offset` (int; pagination — see max offset below)
  - `sort_on` (`score | created | price | updated`) — we call once per probe
    with `sort_on=score` (rank by Etsy's relevancy proxy) and once with
    `sort_on=created` (newest-first, for freshness signals)
  - `sort_order` (`asc | desc`, default `desc`)
  - `taxonomy_id` (int, optional) — passed when a probe constrains to a
    specific category branch
- **Params we explicitly omit:** `min_price`, `max_price`, `shop_location`,
  `region`, `geo_*`, `latitude`, `longitude`, `lat_lng_to_radius`. We post-
  filter price in mining; geo filters skew US-only and we want global signal.
- **Response (top level):**
  - `count` (int, total available across all offsets)
  - `results` (array of listing objects)
  - `pagination.effective_limit`, `effective_offset`, `next_offset`
- **Per-listing fields we extract:**
  - `listing_id` (int) → `EtsyListing.listing_id`
  - `title` (string) → `EtsyListing.title`
  - `tags` (string[]) → `EtsyListing.tags` (lowercased in mining)
  - `price.amount`, `price.divisor`, `price.currency_code` → split into
    `EtsyListing.price_usd` (only populated when `currency_code == "USD"`)
    and `EtsyListing.currency_code`
  - `num_favorers` (int)
  - `views` (int) — present on v3 listings/active responses
  - `shop_id` (int) — used for HHI shop concentration in mining
  - `taxonomy_id` (int, leaf node) → stored in `EtsyListing.taxonomy_path`
    as `[taxonomy_id]`. Full path resolution requires a separate
    `/buyer-taxonomy/nodes` lookup; mining can do this lazily.
  - `is_digital` (bool)
  - `is_personalizable` (bool)
  - `created_timestamp` (Unix epoch int) → mapped to `EtsyListing.creation_tsz`
    for backward compatibility with the old field name. v2 used `creation_tsz`;
    v3 renamed to `created_timestamp`. Old API responses may still use the
    legacy name — the client checks both.
  - `url` (string) → `EtsyListing.url`
- **Max offset:** **12,000.** Past this, Etsy returns 400 / empty results.
  Our pagination helper logs a warning and stops at 12,000 rather than raising.
- **Quirks:**
  - `sort_on=score` is Etsy's internal relevance proxy. It does NOT exactly
    mirror what a buyer sees on `etsy.com/search` — that flow uses
    additional personalization signals not exposed in the API. Treat scores
    as directional, not authoritative.
  - `views` is sometimes 0 for very new listings; don't divide by it.
  - `creation_tsz` vs `created_timestamp`: as above. Parse both; prefer the
    v3 name when both are present.

### `GET /buyer-taxonomy/nodes`

Returns the full Etsy buyer taxonomy tree.

- **URL:** `https://openapi.etsy.com/v3/application/buyer-taxonomy/nodes`
- **Auth:** `x-api-key` header only
- **Params:** none
- **Response:** `{count, results: [{id, level, name, parent_id, path: [str], ...}]}`
- **Cache TTL:** weekly (the tree changes rarely). The default 24h cache is
  bypassed for this endpoint.
- **Used for:** resolving `taxonomy_id` → human-readable path in mining when
  the POD-feasibility heuristic needs to penalize non-apparel categories.

### `GET /listings/{listing_id}` (sparingly)

Hydrate a single listing when we need details not in the search response
(e.g. processing a specific outlier from `evidence_listing_ids`).

- **URL:** `https://openapi.etsy.com/v3/application/listings/{listing_id}`
- **Auth:** `x-api-key` header only
- **Response:** same listing shape as `/listings/active` results
- **Used for:** rare on-demand lookups; not in the hot research path.

## What this client does NOT do

- No OAuth flows (write operations live in Printify, not Etsy).
- No favoriting, messaging, or ads endpoints.
- No multi-shop search — we don't filter by `shop_id` at the search level
  (we measure shop concentration after-the-fact in mining).
