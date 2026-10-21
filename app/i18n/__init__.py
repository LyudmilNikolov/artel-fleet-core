import json
from pathlib import Path

from app.i18n.locales import SUPPORTED_LANGUAGES, normalize_language

_translations: dict[str, dict[str, str]] = {}

I18N_DIR = Path(__file__).parent


def load_translations():
    global _translations
    translations = {}
    for language in SUPPORTED_LANGUAGES:
        with open(I18N_DIR / language / "messages.json", encoding="utf-8") as f:
            translations[language] = json.load(f)
    _translations = translations


def _(key: str, lang: str | None = None, **kwargs) -> str:
    lang = normalize_language(lang)
    translations = _translations.get(lang, {})
    text = translations.get(key, _translations.get("en", {}).get(key, key))
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, IndexError):
            pass
    return text


def get_available_languages() -> list[dict[str, str]]:
    return [{"code": "bg", "name": "Български"}, {"code": "en", "name": "English"}]


load_translations()
