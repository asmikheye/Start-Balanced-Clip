"""Single source of truth for operator-facing runtime defaults."""

DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"
DEFAULT_GEMINI_FALLBACK_MODELS = (
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
)
DEFAULT_GEMINI_RETRY_MODEL = "gemini-3.5-flash-lite"


def default_gemini_fallback_csv() -> str:
    return ",".join(DEFAULT_GEMINI_FALLBACK_MODELS)
