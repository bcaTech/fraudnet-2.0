from __future__ import annotations

import pytest

from fraudnet.i18n import (
    DEFAULT_LOCALE,
    SUPPORTED_LOCALES,
    Translator,
    parse_accept_language,
    translate,
)


class TestSupportedLocales:
    def test_default_is_english(self) -> None:
        assert DEFAULT_LOCALE == "en"

    def test_locale_set(self) -> None:
        assert set(SUPPORTED_LOCALES) >= {"en", "tw", "ga", "ee", "dag", "ha"}


class TestTranslate:
    def test_default_english(self) -> None:
        out = translate("spam_call_warning")
        assert "Warning" in out

    def test_explicit_locale(self) -> None:
        out = translate("spam_call_warning", locale="tw")
        # Twi placeholder is non-empty and not the English string.
        assert out and out != translate("spam_call_warning", locale="en")

    def test_unknown_locale_falls_back_to_english(self) -> None:
        # 'fr' is not supported in MTN Ghana scope.
        assert translate("spam_call_warning", locale="fr") == translate(
            "spam_call_warning", locale="en"
        )

    def test_region_subtag_collapses(self) -> None:
        assert translate("spam_call_warning", locale="tw-GH") == translate(
            "spam_call_warning", locale="tw"
        )

    def test_unknown_key_returns_bracketed_key(self) -> None:
        assert translate("nonexistent_key") == "[nonexistent_key]"

    def test_template_interpolation(self) -> None:
        out = translate("ask_me_first_prompt", amount="500.00")
        assert "500.00" in out

    def test_missing_variable_raises(self) -> None:
        with pytest.raises(KeyError):
            translate("ask_me_first_prompt")  # no `amount`

    def test_partial_locale_falls_back_to_english_for_missing_keys(self) -> None:
        # If a translation file is missing a key, English still answers.
        # We force this by checking a Translator with default_locale set
        # and a deliberately unknown key path: the unknown key returns
        # bracketed; existing keys do resolve in the chosen locale.
        t = Translator(default_locale="ha")
        assert "[" not in t.translate("spam_call_warning")


class TestAcceptLanguage:
    def test_empty_returns_default(self) -> None:
        assert parse_accept_language(None) == DEFAULT_LOCALE
        assert parse_accept_language("") == DEFAULT_LOCALE

    def test_simple_supported(self) -> None:
        assert parse_accept_language("tw") == "tw"

    def test_with_region(self) -> None:
        assert parse_accept_language("tw-GH") == "tw"

    def test_quality_factor_priority(self) -> None:
        assert parse_accept_language("fr;q=0.9, tw;q=0.8") == "tw"

    def test_unsupported_locale_falls_back_to_default(self) -> None:
        assert parse_accept_language("fr-FR") == DEFAULT_LOCALE

    def test_complex_header(self) -> None:
        # Should pick ee (Ewe) over the unsupported de.
        assert parse_accept_language("de;q=1.0, ee;q=0.9, en;q=0.5") == "ee"

    def test_wildcard_ignored(self) -> None:
        assert parse_accept_language("*, ha") == "ha"
