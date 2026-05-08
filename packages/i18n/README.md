# fraudnet-i18n

Multi-language alerts library — Ghana-first.

## Supported locales

| Code | Language |
|---|---|
| `en` | English (canonical) |
| `tw` | Twi |
| `ga` | Ga |
| `ee` | Ewe |
| `dag` | Dagbani |
| `ha` | Hausa |

Non-English translations are placeholder text — pending professional
review (see the `_meta` field in each non-English JSON). The shape and
key set match English; missing keys fall back to English at runtime.

## Use

```python
from fraudnet.i18n import translate, parse_accept_language

translate("ask_me_first_prompt", locale="tw", amount="500.00")
# → "Wo na woma kwan maa GHS 500.00 sika dwumadie yi anaa?"

# In FastAPI:
locale = parse_accept_language(request.headers.get("accept-language"))
msg = translate("spam_call_warning", locale=locale)
```

## Adding a key

1. Add the message to `locales/en.json` (canonical).
2. Add a placeholder to every other locale file with the same `{variable}` tokens.
3. Add a test in `translator_test.py` covering the new key + interpolation.
4. File a translation request with the localisation team — when the
   reviewed string lands, replace the placeholder.
