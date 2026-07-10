"""OAuth 2.0 sign-in for Google (OpenID Connect) and Strava.

Implemented directly over httpx so the token exchange and identity fetch are
plain functions — easy to reason about and to stub in tests.
"""
import secrets
from datetime import timedelta
from urllib.parse import urlencode

import httpx

from .. import config, utils


class OAuthError(Exception):
    """OAuth flow failure; the message is safe to show the user."""


PROVIDERS = {
    "google": {
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "userinfo_url": "https://openidconnect.googleapis.com/v1/userinfo",
        "scope": "openid email profile",
    },
    "strava": {
        "authorize_url": "https://www.strava.com/oauth/authorize",
        "token_url": "https://www.strava.com/oauth/token",
        "scope": "read,activity:read_all",
    },
}


def is_configured(provider: str) -> bool:
    client_id, client_secret = _credentials(provider)
    return bool(client_id and client_secret)


def _credentials(provider: str) -> tuple[str, str]:
    if provider == "google":
        return config.GOOGLE_CLIENT_ID, config.GOOGLE_CLIENT_SECRET
    if provider == "strava":
        return config.STRAVA_CLIENT_ID, config.STRAVA_CLIENT_SECRET
    raise OAuthError(f"Unknown provider '{provider}'.")


def redirect_uri(provider: str) -> str:
    return f"{config.PUBLIC_BASE_URL}/auth/{provider}/callback"


def new_state() -> str:
    return secrets.token_urlsafe(24)


def authorize_url(provider: str, state: str) -> str:
    client_id, _ = _credentials(provider)
    if not is_configured(provider):
        raise OAuthError(
            f"{provider.title()} sign-in is not configured. "
            f"Set {provider.upper()}_CLIENT_ID and {provider.upper()}_CLIENT_SECRET."
        )
    spec = PROVIDERS[provider]
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri(provider),
        "response_type": "code",
        "state": state,
        "scope": spec["scope"],
    }
    if provider == "google":
        params["access_type"] = "offline"
        params["prompt"] = "select_account"
    if provider == "strava":
        params["approval_prompt"] = "auto"
    return f"{spec['authorize_url']}?{urlencode(params)}"


def exchange_code(provider: str, code: str) -> dict:
    """Trade the authorization code for tokens. Returns the raw token payload."""
    client_id, client_secret = _credentials(provider)
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
    }
    if provider == "google":
        data["redirect_uri"] = redirect_uri(provider)
    response = httpx.post(PROVIDERS[provider]["token_url"], data=data, timeout=15)
    if response.status_code != 200:
        raise OAuthError(f"{provider.title()} rejected the sign-in (token exchange failed).")
    return response.json()


def fetch_identity(provider: str, token_payload: dict) -> dict:
    """Normalize provider identity: provider_id, email, name, plus token fields."""
    expires_at = None
    if token_payload.get("expires_in"):
        expires_at = utils.now() + timedelta(seconds=int(token_payload["expires_in"]))
    elif token_payload.get("expires_at"):  # strava sends an absolute epoch
        from datetime import datetime
        expires_at = datetime.fromtimestamp(int(token_payload["expires_at"]), tz=utils.TZ).replace(tzinfo=None)

    base = {
        "access_token": token_payload.get("access_token", ""),
        "refresh_token": token_payload.get("refresh_token", ""),
        "expires_at": expires_at,
    }

    if provider == "google":
        response = httpx.get(
            PROVIDERS["google"]["userinfo_url"],
            headers={"Authorization": f"Bearer {base['access_token']}"},
            timeout=15,
        )
        if response.status_code != 200:
            raise OAuthError("Could not read your Google profile.")
        info = response.json()
        if not info.get("email"):
            raise OAuthError("Google did not share an email address for this account.")
        return {**base, "provider_id": str(info["sub"]), "email": info["email"].lower(),
                "name": info.get("name") or info["email"].split("@")[0]}

    if provider == "strava":
        athlete = token_payload.get("athlete") or {}
        if not athlete.get("id"):
            raise OAuthError("Strava did not return an athlete profile.")
        name = f"{athlete.get('firstname', '')} {athlete.get('lastname', '')}".strip() or f"Strava athlete {athlete['id']}"
        # Strava does not expose email; synthesize a stable local identifier
        email = f"strava-{athlete['id']}@users.sanctuary.club"
        return {**base, "provider_id": str(athlete["id"]), "email": email, "name": name}

    raise OAuthError(f"Unknown provider '{provider}'.")
