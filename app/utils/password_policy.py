"""One password policy for every place that sets a password (API, web forms, CLI, services)."""

from app.services.errors import DomainError

MIN_LENGTH = 10
MAX_LENGTH = 128


def validate_password(password: str, username: str | None = None) -> None:
    """Raise a DomainError when the password is too short, too long, or the same as the username."""
    if not MIN_LENGTH <= len(password or "") <= MAX_LENGTH:
        raise DomainError("error.user_password_short", min=MIN_LENGTH, max=MAX_LENGTH)
    if username and password.casefold() == username.casefold():
        raise DomainError("error.password_is_username")
