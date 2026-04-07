import fal_client
import os

FAL_KEY = os.environ["FAL_KEY"]
os.environ["FAL_KEY"] = FAL_KEY  # fal_client reads this automatically


def generate_image(prompt: str, style: str = "graphic_tee") -> str:
    """Returns a public image URL"""

    # Route by design type
    if style == "graphic_tee":
        # Ideogram v3 - best for bold graphic/poster aesthetic
        result = fal_client.run(
            "fal-ai/ideogram/v3",
            arguments={
                "prompt": (
                    f"Bold screen print graphic for apparel, flat illustration, "
                    f"{prompt}, "
                    f"isolated on pure white background, high contrast, "
                    f"no gradients, no shadows, no photography, no 3D"
                ),
                "aspect_ratio": "1:1",
                "style": "design",  # Ideogram style preset
                "rendering_speed": "QUALITY",
            }
        )
    elif style == "illustration":
        # Recraft V4 - for cleaner vector-adjacent illustration runs
        result = fal_client.run(
            "fal-ai/recraft-v3",
            arguments={
                "prompt": (
                    f"vector illustration, bold graphic design, "
                    f"{prompt}, "
                    f"isolated on white, flat colors, print-ready"
                ),
                "style": "vector_illustration",
                "image_size": "square_hd",
            }
        )

    return result["images"][0]["url"]