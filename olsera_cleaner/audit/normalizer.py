"""Conservative Indonesian product-name suggestions; NEVER updates Olsera."""
import re
import unicodedata

# Controlled, context-neutral spellings only. Ambiguous variants need human review.
TERMS = {
    "GRM": "GR", "GRAM": "GR", "G": "GR", "KILOGRAM": "KG",
    "LITER": "L", "LITRE": "L", "MILLILITER": "ML", "MILILITER": "ML",
    "PCS.": "PCS", "SACH": "SACHET", "SCH": "SACHET",
}
AMBIGUOUS = {"GRG", "SPC", "SP", "ORG", "REG", "KCL", "BRT", "LMN", "BTL"}
UNIT = re.compile(r"(?<!\w)(\d+(?:[.,]\d+)?)\s*(GRAM|GRM|GR|G|KG|KILOGRAM|ML|MILLILITER|MILILITER|LITER|LITRE|L|PCS|SACHET|SACH|SCH|PACK)(?!\w)", re.I)
SPACES = re.compile(r"\s+")


def normalize_name(name):
    if not isinstance(name, str):
        return "", []
    original = name
    value = unicodedata.normalize("NFKC", name).strip().upper()
    # Dashes and punctuation meaningful to brands/variants are preserved.
    value = SPACES.sub(" ", value)
    def replace_unit(match):
        amount, unit = match.groups()
        # Preserve decimals as supplied; don't guess whether comma means decimal.
        return f"{amount} {TERMS.get(unit.upper(), unit.upper())}"
    value = UNIT.sub(replace_unit, value)
    value = SPACES.sub(" ", value).strip()
    flags = []
    if any(re.search(rf"(?<!\w){re.escape(t)}(?!\w)", value) for t in AMBIGUOUS):
        flags.append("ABBREVIATION_AMBIGUOUS")
    if value != original:
        flags.append("NAME_FORMAT")
    return value, flags
