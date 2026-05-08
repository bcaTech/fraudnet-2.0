"""Translator — locale resolution + template interpolation.

Locale catalogue is loaded once at import time from JSON files in
`packages/i18n/src/fraudnet/i18n/locales/<locale>.json`. The default
language (English) is the source-of-truth for the message-key set; other
locales may have partial coverage and fall back to English.
"""

from __future__ import annotations

import json
import re
from importlib.resources import files as _resource_files
from typing import Final, Mapping

DEFAULT_LOCALE: Final[str] = "en"

# Order matters — the first locale supported is the default.
SUPPORTED_LOCALES: Final[tuple[str, ...]] = ("en", "tw", "ga", "ee", "dag", "ha")


_LOCALE_RE = re.compile(r"^[a-z]{2,3}(?:[-_][A-Za-z0-9]+)?$")


def _load_catalogue() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    pkg = _resource_files("fraudnet.i18n").joinpath("locales")
    for loc in SUPPORTED_LOCALES:
        path = pkg.joinpath(f"{loc}.json")
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            out[loc] = {}
            continue
        out[loc] = dict(json.loads(text))
    return out


_CATALOGUE: dict[str, dict[str, str]] = _load_catalogue()


class Translator:
    """Stateful translator — same catalogue, configurable default locale."""

    def __init__(self, *, default_locale: str = DEFAULT_LOCALE) -> None:
        if default_locale not in SUPPORTED_LOCALES:
            raise ValueError(f"unsupported locale: {default_locale}")
        self._default = default_locale

    def translate(self, key: str, locale: str | None = None, **variables: object) -> str:
        chosen = self._resolve_locale(locale) if locale else self._default
        catalogue_chain: list[Mapping[str, str]] = []
        if chosen in _CATALOGUE:
            catalogue_chain.append(_CATALOGUE[chosen])
        # Always include English as a final fallback (unless we already are).
        if chosen != DEFAULT_LOCALE:
            catalogue_chain.append(_CATALOGUE[DEFAULT_LOCALE])

        for cat in catalogue_chain:
            tpl = cat.get(key)
            if tpl is None:
                continue
            try:
                return tpl.format(**variables)
            except KeyError as exc:
                # A missing placeholder is a programmer bug — surface it
                # rather than fall back silently to a different locale.
                raise KeyError(f"missing variable {exc!s} for i18n key {key!r}") from None
        return f"[{key}]"

    @staticmethod
    def _resolve_locale(raw: str) -> str:
        if not raw:
            return DEFAULT_LOCALE
        loc = raw.lower().strip()
        if not _LOCALE_RE.match(loc):
            return DEFAULT_LOCALE
        if loc in SUPPORTED_LOCALES:
            return loc
        # Strip region tag (tw-GH → tw).
        base = loc.split("-", 1)[0].split("_", 1)[0]
        if base in SUPPORTED_LOCALES:
            return base
        return DEFAULT_LOCALE


_DEFAULT_TRANSLATOR = Translator()


def translate(key: str, locale: str | None = None, **variables: object) -> str:
    """Module-level convenience — uses the default English translator."""
    return _DEFAULT_TRANSLATOR.translate(key, locale=locale, **variables)


def raw_template(key: str, locale: str | None = None) -> str:
    """Return the unrendered template string for a key/locale pair.

    Useful for bulk dumps where the *caller* renders placeholders at
    delivery time. Falls back to English then to a bracketed key.
    """
    chosen = Translator._resolve_locale(locale) if locale else DEFAULT_LOCALE
    if chosen in _CATALOGUE:
        v = _CATALOGUE[chosen].get(key)
        if v is not None:
            return v
    return _CATALOGUE[DEFAULT_LOCALE].get(key, f"[{key}]")


def parse_accept_language(header: str | None) -> str:
    """Parse an Accept-Language header into a single supported locale.

    Picks the first listed tag whose base language is in SUPPORTED_LOCALES.
    Quality factors are honoured for ordering. Returns DEFAULT_LOCALE on
    no match or empty header.
    """
    if not header:
        return DEFAULT_LOCALE
    candidates: list[tuple[float, int, str]] = []
    for index, raw in enumerate(header.split(",")):
        s = raw.strip()
        if not s:
            continue
        q = 1.0
        if ";" in s:
            tag, *params = (p.strip() for p in s.split(";"))
            for p in params:
                if p.startswith("q="):
                    try:
                        q = float(p[2:])
                    except ValueError:
                        q = 0.0
        else:
            tag = s
        if not tag or tag == "*":
            continue
        candidates.append((q, -index, tag))
    candidates.sort(reverse=True)
    for _q, _i, tag in candidates:
        resolved = Translator._resolve_locale(tag)
        if resolved != DEFAULT_LOCALE or tag.lower().startswith("en"):
            return resolved
    return DEFAULT_LOCALE
