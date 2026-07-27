"""Authentication: a tiny in-memory user directory and login checks.

Demo plumbing only -- passwords are plain text on purpose; the point of
the case is the shop logic, not security.
"""


class AuthError(ValueError):
    """Login failure or missing authentication."""


USERS = {
    "alice": {"login": "alice", "password": "wonderland",
              "email": "alice@example.com", "name": "Alice"},
    "bob": {"login": "bob", "password": "builder",
            "email": "bob@example.com", "name": "Bob"},
}


def authenticate(login, password):
    """Return the user record on success, raise AuthError otherwise."""
    user = USERS.get(login)
    if user is None or user["password"] != password:
        raise AuthError("invalid login or password")
    return user


def require_user(user):
    """Guard for checkout paths: the caller must be an authenticated user."""
    if not isinstance(user, dict) or user.get("login") not in USERS:
        raise AuthError("authentication required")
    return user
