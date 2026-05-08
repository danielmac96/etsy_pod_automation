from __future__ import annotations

from pydantic import BaseModel, Field


class EtsyListing(BaseModel):
    listing_id: int
    title: str
    tags: list[str] = Field(default_factory=list)
    price_usd: float | None = None
    currency_code: str | None = None
    num_favorers: int = 0
    views: int | None = None
    shop_id: int | None = None
    taxonomy_path: list | None = None
    is_digital: bool | None = None
    is_personalizable: bool | None = None
    creation_tsz: int | None = None
    url: str = ""

    @classmethod
    def from_v3(cls, raw: dict) -> "EtsyListing":
        """Parse a v3 /listings/active result dict into an EtsyListing."""
        price = raw.get("price") or {}
        currency = price.get("currency_code")
        amount = price.get("amount")
        divisor = price.get("divisor") or 100
        price_usd: float | None = None
        if currency == "USD" and amount is not None:
            price_usd = round(amount / divisor, 2)
        creation = raw.get("created_timestamp") or raw.get("creation_tsz")
        taxonomy_id = raw.get("taxonomy_id")
        taxonomy_path = [taxonomy_id] if taxonomy_id is not None else None
        return cls(
            listing_id=raw["listing_id"],
            title=raw.get("title", ""),
            tags=list(raw.get("tags") or []),
            price_usd=price_usd,
            currency_code=currency,
            num_favorers=int(raw.get("num_favorers") or 0),
            views=raw.get("views"),
            shop_id=raw.get("shop_id"),
            taxonomy_path=taxonomy_path,
            is_digital=raw.get("is_digital"),
            is_personalizable=raw.get("is_personalizable"),
            creation_tsz=creation,
            url=raw.get("url") or f"https://www.etsy.com/listing/{raw['listing_id']}",
        )


class Evidence(BaseModel):
    etsy_tag_overlap: list[str] = Field(default_factory=list)
    search_volume_signal: str = "unknown"  # high | medium | low
    saturation: str = "unknown"           # high | medium | low
    supporting_listings: list[dict] = Field(default_factory=list)
    price_tier_usd: list[float] = Field(default_factory=list)


class DesignBriefContent(BaseModel):
    headline_text: str
    visual_concept: str
    style_tags: list[str]
    color_palette_hint: str
    target_buyer: str


class DesignBrief(BaseModel):
    brief_id: str
    concept_id: str
    theme_id: str
    run_id: str
    concept_name: str
    rank: int
    category: str  # maps to one of the 5 CATEGORIES in script 02
    evidence: Evidence
    design_brief: DesignBriefContent
    image_prompt_seed: str


class ResearchRun(BaseModel):
    run_id: str
    timestamp: str
    briefs: list[DesignBrief]
