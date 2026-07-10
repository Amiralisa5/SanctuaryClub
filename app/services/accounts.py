"""Account lifecycle: OAuth sign-in resolution and password reset tokens."""
import hashlib
import secrets
from datetime import timedelta

from sqlalchemy import select

from .. import config, utils
from ..audit import log_action
from ..models import (
    Client,
    HealthConnection,
    OAuthAccount,
    PasswordResetToken,
    Role,
    User,
)
from . import notifications


def resolve_oauth_user(db, provider: str, identity: dict) -> tuple[User, bool]:
    """Find or create the local user for an OAuth identity.

    Order: existing linked account -> existing user with same email (link) ->
    brand-new CLIENT signup (no coach yet; admin assigns one).
    Returns (user, created).
    """
    account = db.scalar(select(OAuthAccount).where(
        OAuthAccount.provider == provider,
        OAuthAccount.provider_account_id == identity["provider_id"],
    ))
    if account:
        _update_tokens(account, identity)
        _sync_health_connection(db, account.user, provider, identity)
        db.commit()
        return account.user, False

    user = db.scalar(select(User).where(User.email == identity["email"]))
    created = False
    if user is None:
        user = User(email=identity["email"], password_hash="",
                    full_name=identity["name"], role=Role.CLIENT)
        db.add(user)
        db.flush()
        db.add(Client(user_id=user.id, coach_id=None))
        created = True

    account = OAuthAccount(user_id=user.id, provider=provider,
                           provider_account_id=identity["provider_id"],
                           email=identity["email"])
    _update_tokens(account, identity)
    db.add(account)
    db.flush()
    _sync_health_connection(db, user, provider, identity)
    log_action(db, user, "auth.oauth_signup" if created else "auth.oauth_link",
               "user", user.id, f"provider={provider}")
    db.commit()
    return user, created


def _update_tokens(account: OAuthAccount, identity: dict) -> None:
    account.access_token = identity.get("access_token", "")
    if identity.get("refresh_token"):
        account.refresh_token = identity["refresh_token"]
    account.expires_at = identity.get("expires_at")
    account.email = identity.get("email", account.email)


def _sync_health_connection(db, user: User, provider: str, identity: dict) -> None:
    """Strava sign-in doubles as a health-data connection for client accounts."""
    if provider != "strava":
        return
    client = db.scalar(select(Client).where(Client.user_id == user.id))
    if client is None:
        return
    connection = db.scalar(select(HealthConnection).where(
        HealthConnection.client_id == client.id, HealthConnection.provider == "strava"))
    if connection is None:
        connection = HealthConnection(client_id=client.id, provider="strava")
        db.add(connection)
    connection.access_token = identity.get("access_token", "")
    if identity.get("refresh_token"):
        connection.refresh_token = identity["refresh_token"]
    connection.expires_at = identity.get("expires_at")
    connection.status = "connected"


# --- Password reset ---

def issue_reset_token(db, user: User) -> str:
    raw = secrets.token_urlsafe(32)
    db.add(PasswordResetToken(
        user_id=user.id,
        token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        expires_at=utils.now() + timedelta(minutes=config.RESET_TOKEN_TTL_MINUTES),
    ))
    log_action(db, user, "auth.reset_requested", "user", user.id)
    return raw


def send_reset_email(db, user: User, raw_token: str) -> None:
    link = f"{config.PUBLIC_BASE_URL}/reset-password?token={raw_token}"
    notifications.send_email(
        db, user.email, "Reset your password — SanctuaryClub",
        f"Hi {user.full_name},\n\n"
        f"Use this link to set a new password (valid for "
        f"{config.RESET_TOKEN_TTL_MINUTES} minutes, single use):\n\n{link}\n\n"
        f"If you didn't request this, you can ignore this email.\n\nSanctuaryClub",
    )


def consume_reset_token(db, raw_token: str) -> User | None:
    """Return the token's user if valid; caller sets the password and commits."""
    token = db.scalar(select(PasswordResetToken).where(
        PasswordResetToken.token_hash == hashlib.sha256(raw_token.encode()).hexdigest()))
    if token is None or token.used or token.expires_at < utils.now():
        return None
    token.used = True
    return token.user


def peek_reset_token(db, raw_token: str) -> User | None:
    """Validity check without consuming (for rendering the reset form)."""
    token = db.scalar(select(PasswordResetToken).where(
        PasswordResetToken.token_hash == hashlib.sha256(raw_token.encode()).hexdigest()))
    if token is None or token.used or token.expires_at < utils.now():
        return None
    return token.user
