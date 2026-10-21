"""Pure request locale resolution; never changes process-wide state."""

from typing import Literal

Language = Literal["bg", "en"]
SUPPORTED_LANGUAGES = ("bg", "en")
DEFAULT_LANGUAGE = "bg"


def normalize_language(value: str | None) -> str:
    return value if value in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


def header_language(header: str) -> str | None:
    candidates = []
    for index, part in enumerate(header.split(",")):
        tag, *parameters = part.strip().split(";")
        quality = 1.0
        for parameter in parameters:
            if parameter.strip().startswith("q="):
                try:
                    quality = float(parameter.strip()[2:])
                except ValueError:
                    quality = 0
        language = tag.lower().split("-")[0]
        if language in SUPPORTED_LANGUAGES and 0 < quality <= 1:
            candidates.append((-quality, index, language))
    return min(candidates)[2] if candidates else None


def resolve_locale(request, user=None) -> str:
    query = request.query_params.get("lang")
    saved = getattr(user, "language", None)
    if request.url.path.startswith("/api/"):
        language = (
            (query if query in SUPPORTED_LANGUAGES else None)
            or header_language(request.headers.get("accept-language", ""))
            or (saved if saved in SUPPORTED_LANGUAGES else None)
            or DEFAULT_LANGUAGE
        )
    else:
        session = request.session.get("lang")
        language = (
            (query if query in SUPPORTED_LANGUAGES else None)
            or (session if session in SUPPORTED_LANGUAGES else None)
            or (saved if saved in SUPPORTED_LANGUAGES else None)
            or DEFAULT_LANGUAGE
        )
        if session not in SUPPORTED_LANGUAGES:
            if user is not None:
                request.session["lang"] = normalize_language(saved)
            else:
                request.session.pop("lang", None)
    request.state.locale = language
    return language
