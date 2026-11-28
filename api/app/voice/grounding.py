"""Deterministic field-value-to-quotation consistency checks for structured extraction."""

import re

_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")

_UNITS: dict[str, int] = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}

_TENS: dict[str, int] = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}

_SCALES: dict[str, int] = {
    "hundred": 100,
    "thousand": 1_000,
    "lakh": 100_000,
    "lakhs": 100_000,
    "lac": 100_000,
    "crore": 10_000_000,
    "crores": 10_000_000,
}

_INR_MARKERS = (
    "₹",
    "rs",
    "inr",
    "rupee",
    "rupees",
    "rupaye",
    "rupaiye",
    "रुपये",
    "रुपए",
    "रुपया",
)

_FOREIGN_CURRENCY_MARKERS = ("$", "usd", "dollar", "dollars", "€", "eur", "euro", "£", "gbp")

_PLATFORM_SYNONYMS: dict[str, tuple[str, ...]] = {
    "swiggy": ("swiggy", "स्विगी"),
    "zomato": ("zomato", "ज़ोमैटो", "जोमैटो"),
    "rapido": ("rapido", "रैपिडो"),
    "uber": ("uber", "उबर", "ऊबर"),
}

_MONTH_GROUPS: tuple[tuple[str, ...], ...] = (
    ("january", "jan"),
    ("february", "feb"),
    ("march", "mar"),
    ("april", "apr"),
    ("may",),
    ("june", "jun"),
    ("july", "jul"),
    ("august", "aug"),
    ("september", "sep", "sept"),
    ("october", "oct"),
    ("november", "nov"),
    ("december", "dec"),
)

_WEEKDAY_GROUPS: tuple[tuple[str, ...], ...] = (
    ("monday", "mon", "somwar", "somvar"),
    ("tuesday", "tue", "tues", "mangalwar", "mangalvar"),
    ("wednesday", "wed", "budhwar", "budhvar"),
    ("thursday", "thu", "thurs", "guruwar", "brihaspatiwar"),
    ("friday", "fri", "shukrawar", "shukravar"),
    ("saturday", "sat", "shaniwar", "shanivar"),
    ("sunday", "sun", "raviwar", "ravivar"),
)

_PAYMENT_CATEGORY_KEYWORDS = (
    "payment",
    "payout",
    "incentive",
    "bonus",
    "earning",
    "earnings",
    "fare",
    "pay",
    "paid",
    "credit",
    "credited",
    "salary",
    "wage",
    "wages",
    "discrepancy",
    "shortfall",
    "short",
    "missing",
    "deduct",
    "deduction",
    "rupee",
    "rupees",
    "inr",
    "rs",
    "₹",
    "kam",
    "paisa",
    "paise",
    "पेमेंट",
    "इंसेंटिव",
    "क्रेडिट",
    "पैसे",
    "बोनस",
    "रुपये",
)

_STOPWORDS = {
    "a",
    "an",
    "the",
    "is",
    "was",
    "are",
    "were",
    "be",
    "been",
    "being",
    "i",
    "my",
    "me",
    "we",
    "our",
    "for",
    "of",
    "to",
    "in",
    "on",
    "at",
    "by",
    "from",
    "with",
    "and",
    "or",
    "but",
    "not",
    "no",
    "all",
    "this",
    "that",
    "it",
    "as",
    "got",
    "have",
    "has",
    "had",
    "did",
    "do",
    "does",
}


def _normalize_digits(text: str) -> str:
    return text.translate(_DEVANAGARI_DIGITS)


def extract_numbers_from_text(text: str) -> list[float]:
    """Extract numeric digit values and English spoken number compounds from text."""
    normalized = _normalize_digits(text)
    found: list[float] = []

    for match in re.finditer(r"(?<![a-zA-Z0-9.])\d+(?:,\d+)*(?:\.\d+)?(?![a-zA-Z0-9.])", normalized):
        try:
            found.append(float(match.group(0).replace(",", "")))
        except ValueError:
            continue

    # Also match currency-prefixed digits like ₹850 or Rs.850
    for match in re.finditer(r"(?:₹|rs\.?|inr)\s*(\d+(?:,\d+)*(?:\.\d+)?)", normalized, flags=re.IGNORECASE):
        try:
            found.append(float(match.group(1).replace(",", "")))
        except ValueError:
            continue

    # Parse English number-word spans (e.g. "eight hundred fifty", "one thousand two hundred")
    tokens = re.findall(r"[a-z]+", normalized.lower())
    current = 0
    total = 0
    in_number = False
    for tok in tokens:
        if tok in _UNITS:
            current += _UNITS[tok]
            in_number = True
        elif tok in _TENS:
            current += _TENS[tok]
            in_number = True
        elif tok == "hundred" and in_number:
            current = (current or 1) * 100
        elif tok in {"thousand", "lakh", "lakhs", "lac", "crore", "crores"} and in_number:
            total += (current or 1) * _SCALES[tok]
            current = 0
        elif tok == "and" and in_number:
            continue
        else:
            if in_number:
                found.append(float(total + current))
                current = 0
                total = 0
                in_number = False
    if in_number:
        found.append(float(total + current))

    return found


def _contains_token_or_symbol(text: str, marker: str) -> bool:
    if not marker.isascii() or not marker.isalnum():
        return marker.lower() in text.lower()
    return re.search(rf"\b{re.escape(marker)}\b", text, flags=re.IGNORECASE) is not None


def _has_inr_marker(text: str) -> bool:
    if "₹" in text:
        return True
    if re.search(r"₹\s*\d", text):
        return True
    if re.search(r"\brs\.?\s*\d", text, flags=re.IGNORECASE):
        return True
    return any(_contains_token_or_symbol(text, marker) for marker in _INR_MARKERS if marker != "₹")


def _has_foreign_currency_marker(text: str) -> bool:
    return any(_contains_token_or_symbol(text, marker) for marker in _FOREIGN_CURRENCY_MARKERS)


def _mentioned_platforms(text: str) -> set[str]:
    lowered = text.lower()
    hits: set[str] = set()
    for platform, aliases in _PLATFORM_SYNONYMS.items():
        for alias in aliases:
            if alias.isascii():
                if re.search(rf"\b{re.escape(alias)}\b", lowered):
                    hits.add(platform)
                    break
            elif alias in lowered:
                hits.add(platform)
                break
    return hits


def _validate_amount_field(value: float, quote: str) -> str | None:
    if _has_foreign_currency_marker(quote) and not _has_inr_marker(quote):
        return "Source quotation specifies a non-INR foreign currency."

    quote_numbers = extract_numbers_from_text(quote)
    if not quote_numbers:
        return f"Extracted amount {value:g} has no numeric support in its source quotation."

    if not any(abs(float(value) - num) < 0.01 for num in quote_numbers):
        return (
            f"Extracted amount {value:g} does not match any numeric value in its source quotation."
        )
    return None


def _validate_currency_field(value: str, quote: str) -> str | None:
    val_lower = value.strip().lower()
    if val_lower in {"inr", "rs", "rs.", "rupee", "rupees", "₹", "रुपये", "रुपए", "रुपया"}:
        if not _has_inr_marker(quote):
            return f"Currency '{value}' is not explicitly stated in its source quotation."
        return None

    # Allow other explicit currency ONLY if explicitly written in the quote
    if _contains_token_or_symbol(quote, val_lower):
        return None
    return f"Currency '{value}' is not supported by its source quotation."


def _validate_platform_field(value: str, quote: str) -> str | None:
    val_lower = value.strip().lower()
    quote_platforms = _mentioned_platforms(quote)

    if val_lower in _PLATFORM_SYNONYMS:
        if val_lower not in quote_platforms:
            return f"Platform '{value}' is not explicitly named in its source quotation."
        return None

    if val_lower == "other":
        if quote_platforms:
            return f"Platform 'other' contradicts named platform(s) {sorted(quote_platforms)} in quotation."
        return None
    return f"Unsupported platform '{value}'."


def _validate_period_field(value: str, quote: str) -> str | None:
    val_norm = _normalize_digits(value).lower()
    quote_norm = _normalize_digits(quote).lower()

    # Every explicit number/year/day in the extracted period must appear in the quote
    val_nums = set(re.findall(r"\d+", val_norm))
    quote_nums = set(re.findall(r"\d+", quote_norm))
    missing_nums = val_nums - quote_nums
    if missing_nums:
        return f"Extracted period contains date/number(s) {sorted(missing_nums)} not present in quotation."

    # Every named month or weekday in extracted period must match a synonym in quote
    for group in (*_MONTH_GROUPS, *_WEEKDAY_GROUPS):
        val_has = any(re.search(rf"\b{re.escape(tok)}\b", val_norm) for tok in group)
        if val_has:
            quote_has = any(re.search(rf"\b{re.escape(tok)}\b", quote_norm) for tok in group)
            if not quote_has:
                return f"Extracted period references '{group[0]}' which is not in its source quotation."

    # Ensure at least one meaningful non-stopword token overlaps
    val_tokens = [t for t in re.findall(r"[a-zA-Z0-9]+", val_norm) if t not in _STOPWORDS]
    quote_tokens = set(re.findall(r"[a-zA-Z0-9]+", quote_norm))
    if val_tokens and not any(t in quote_tokens for t in val_tokens):
        return "Extracted period has no overlapping temporal terms with its source quotation."
    return None


def _validate_exact_token_field(value: str, quote: str, label: str) -> str | None:
    cleaned = value.strip()
    if cleaned.lower() not in quote.lower():
        return f"{label} '{cleaned}' does not appear verbatim in its source quotation."
    return None


def _validate_category_field(value: str, quote: str) -> str | None:
    if value != "payment_discrepancy":
        return f"Unsupported grievance category '{value}'."
    lowered = quote.lower()
    if not any(_contains_token_or_symbol(lowered, kw) for kw in _PAYMENT_CATEGORY_KEYWORDS):
        return "Source quotation for payment_discrepancy lacks payment or incentive discrepancy evidence."
    return None


def _validate_summary_field(field_name: str, value: str, quote: str) -> str | None:
    # 1. No invented digits/numbers in summaries
    val_nums = set(re.findall(r"\d+(?:\.\d+)?", _normalize_digits(value).replace(",", "")))
    quote_nums = {f"{n:g}" for n in extract_numbers_from_text(quote)}
    quote_raw_nums = set(re.findall(r"\d+(?:\.\d+)?", _normalize_digits(quote).replace(",", "")))
    allowed_nums = quote_nums | quote_raw_nums
    if val_nums and not val_nums.issubset(allowed_nums):
        missing = sorted(val_nums - allowed_nums)
        return f"Summary for '{field_name}' contains number(s) {missing} not present in its source quotation."

    # 2. No invented platform names in summaries
    val_platforms = _mentioned_platforms(value)
    quote_platforms = _mentioned_platforms(quote)
    if val_platforms and not val_platforms.issubset(quote_platforms):
        invented = sorted(val_platforms - quote_platforms)
        return f"Summary for '{field_name}' names platform(s) {invented} absent from its source quotation."

    # 3. Lexical / stem overlap check for Latin tokens when quote has Latin content words
    val_words = [w for w in re.findall(r"[a-zA-Z]{3,}", value.lower()) if w not in _STOPWORDS]
    quote_words = [w for w in re.findall(r"[a-zA-Z]{3,}", quote.lower()) if w not in _STOPWORDS]
    if val_words and quote_words:
        quote_stems = {w[:4] for w in quote_words}
        matched = sum(1 for w in val_words if w[:4] in quote_stems or w in quote_words)
        if matched == 0 or (matched / len(val_words)) < 0.35:
            return f"Summary for '{field_name}' is not lexically supported by its source quotation."
    return None


def validate_field_value_consistency(
    *,
    field_name: str,
    value: object,
    source_quote: str,
) -> str | None:
    """Return a human-readable rejection reason if extracted value contradicts or lacks quote support."""
    if field_name in {"discrepancy_amount_inr", "expected_amount_inr", "received_amount_inr"}:
        if isinstance(value, (int, float)):
            return _validate_amount_field(float(value), source_quote)
        return "Numeric amount expected."

    if field_name == "currency" and isinstance(value, str):
        return _validate_currency_field(value, source_quote)

    if field_name == "platform" and isinstance(value, str):
        return _validate_platform_field(value, source_quote)

    if field_name == "payout_cycle_raw" and isinstance(value, str):
        return _validate_period_field(value, source_quote)

    if field_name in {"prior_ticket_id", "work_city"} and isinstance(value, str):
        return _validate_exact_token_field(value, source_quote, field_name)

    if field_name == "grievance_category" and isinstance(value, str):
        return _validate_category_field(value, source_quote)

    if field_name in {
        "issue_description",
        "work_completed_summary",
        "support_contact_summary",
        "support_response_summary",
        "incentive_scheme_name",
    } and isinstance(value, str):
        return _validate_summary_field(field_name, value, source_quote)

    return None
