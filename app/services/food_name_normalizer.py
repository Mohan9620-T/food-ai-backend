import re

FOOD_NAME_ALIASES = {
    "round white steamed cake": "idli",
    "steamed rice cake": "idli",
}


def normalize_food_name(name: str) -> str:
    """Map tested visual descriptions to canonical food names."""
    cleaned = " ".join(re.findall(r"[a-z0-9]+", name.lower()))
    singular = " ".join(
        word[:-1] if word.endswith("s") and len(word) > 3 else word for word in cleaned.split()
    )
    return FOOD_NAME_ALIASES.get(singular, cleaned)
