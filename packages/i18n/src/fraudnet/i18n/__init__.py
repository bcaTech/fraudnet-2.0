"""FraudNet 2.0 multi-language alerts.

Supported locales (Ghana-first):
    en   — English (canonical, all messages defined)
    tw   — Twi
    ga   — Ga
    ee   — Ewe
    dag  — Dagbani
    ha   — Hausa

Resolution order on `translate(key, locale, **vars)`:
    1. Requested locale.
    2. Exact base language match (e.g. `tw-GH` → `tw`).
    3. English fallback.
    4. The key itself, in [brackets], if no fallback hits.

Translation strings use Python str.format() placeholders — `{amount}`,
`{recipient}` etc.
"""

from fraudnet.i18n.translator import (
    SUPPORTED_LOCALES,
    DEFAULT_LOCALE,
    parse_accept_language,
    raw_template,
    translate,
    Translator,
)

__all__ = [
    "DEFAULT_LOCALE",
    "SUPPORTED_LOCALES",
    "Translator",
    "parse_accept_language",
    "raw_template",
    "translate",
]
