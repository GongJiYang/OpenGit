"""
Agent Authentication Utilities

Core utility functions for API key generation, hashing, and claim code generation.
"""

import hashlib
import os
import secrets
import string
from datetime import datetime, timedelta
from typing import Tuple, Optional
from uuid import UUID

import bcrypt


# ============== Constants ==============

API_KEY_PREFIX = "agenthub_live_"
API_KEY_LENGTH = 32  # Random part length
API_KEY_PREFIX_DISPLAY_LENGTH = 12  # Prefix length stored for lookup

CLAIM_CODE_LENGTH = 8
CLAIM_EXPIRATION_HOURS = 24

EMAIL_VERIFY_TOKEN_LENGTH = 32
EMAIL_VERIFY_EXPIRATION_MINUTES = 30

BCRYPT_ROUNDS = 12


# ============== API Key Utilities ==============

def generate_api_key() -> str:
    """
    Generate a new API key.

    Format: agenthub_live_{32 random alphanumeric characters}
    Example: agenthub_live_x8F2kL9mNpQrStUvWxYz1234567890ab

    Returns:
        str: Full API key (plaintext)
    """
    random_part = ''.join(
        secrets.choice(string.ascii_letters + string.digits)
        for _ in range(API_KEY_LENGTH)
    )
    return f"{API_KEY_PREFIX}{random_part}"


def hash_api_key(api_key: str) -> str:
    """
    Hash an API key using bcrypt.

    Args:
        api_key: Plain text API key

    Returns:
        str: bcrypt hash string
    """
    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    hashed = bcrypt.hashpw(api_key.encode('utf-8'), salt)
    return hashed.decode('utf-8')


def verify_api_key(api_key: str, hashed_key: str) -> bool:
    """
    Verify an API key against its hash.

    Args:
        api_key: Plain text API key to verify
        hashed_key: Stored bcrypt hash

    Returns:
        bool: True if key matches, False otherwise
    """
    try:
        return bcrypt.checkpw(api_key.encode('utf-8'), hashed_key.encode('utf-8'))
    except Exception:
        return False


def get_legacy_api_key_prefix(api_key: str) -> str:
    """
    Legacy prefix: first N characters of the full API key.

    NOTE: This is effectively constant for keys that share the same
    API_KEY_PREFIX and caused collisions. Kept for backward compatibility.
    """
    if not api_key:
        return ""
    return api_key[:API_KEY_PREFIX_DISPLAY_LENGTH]


def get_api_key_prefix(api_key: str) -> str:
    """
    Get a stable prefix used for lookup.

    We now take the first N chars of the random part so different keys
    do not collide on the constant "agenthub_live_" prefix.
    """
    if not api_key:
        return ""
    if api_key.startswith(API_KEY_PREFIX):
        random_part = api_key[len(API_KEY_PREFIX):]
        if len(random_part) >= API_KEY_PREFIX_DISPLAY_LENGTH:
            return random_part[:API_KEY_PREFIX_DISPLAY_LENGTH]
    return api_key[:API_KEY_PREFIX_DISPLAY_LENGTH]


# ============== Claim Code Utilities ==============

def generate_claim_code() -> str:
    """
    Generate a random 8-character claim code.

    Uses uppercase letters and digits only for better readability.

    Returns:
        str: 8-character claim code
    """
    return ''.join(
        secrets.choice(string.ascii_uppercase + string.digits)
        for _ in range(CLAIM_CODE_LENGTH)
    )


def generate_claim_url(claim_code: str, base_url: str = "") -> str:
    """
    Generate the full claim URL.

    Args:
        claim_code: The claim code
        base_url: Optional base URL (e.g., "https://agenthub.dev")

    Returns:
        str: Full claim URL path
    """
    path = f"/api/v1/agents/claim/{claim_code}"
    if base_url:
        return f"{base_url.rstrip('/')}{path}"
    return path


def calculate_claim_expiration() -> datetime:
    """
    Calculate the claim expiration timestamp.

    Returns:
        datetime: Expiration timestamp (24 hours from now)
    """
    return datetime.utcnow() + timedelta(hours=CLAIM_EXPIRATION_HOURS)


def is_claim_expired(expires_at: datetime) -> bool:
    """
    Check if a claim has expired.

    Args:
        expires_at: Expiration timestamp

    Returns:
        bool: True if expired, False otherwise
    """
    return datetime.utcnow() > expires_at


# ============== Email Verification Utilities ==============

def generate_email_verify_token() -> str:
    """
    Generate a secure token for email verification.

    Returns:
        str: URL-safe random token (43 characters)
    """
    return secrets.token_urlsafe(EMAIL_VERIFY_TOKEN_LENGTH)


def calculate_email_verify_expiration() -> datetime:
    """
    Calculate email verification token expiration timestamp.

    Returns:
        datetime: Expiration timestamp (30 minutes from now)
    """
    return datetime.utcnow() + timedelta(minutes=EMAIL_VERIFY_EXPIRATION_MINUTES)


def is_email_verify_expired(expires_at: datetime) -> bool:
    """
    Check if an email verification token has expired.

    Args:
        expires_at: Token expiration timestamp

    Returns:
        bool: True if expired, False otherwise
    """
    return datetime.utcnow() > expires_at


# ============== Token Utilities ==============

def generate_oauth_state_token() -> str:
    """
    Generate a secure state token for OAuth flow.

    Returns:
        str: URL-safe random token
    """
    return secrets.token_urlsafe(32)


def generate_session_token() -> str:
    """
    Generate a session token for authenticated sessions.

    Returns:
        str: URL-safe random token
    """
    return secrets.token_urlsafe(64)


# ============== Validation Utilities ==============

def is_valid_api_key_format(api_key: str) -> bool:
    """
    Validate API key format.

    Args:
        api_key: API key to validate

    Returns:
        bool: True if format is valid
    """
    if not api_key:
        return False
    if not api_key.startswith(API_KEY_PREFIX):
        return False
    random_part = api_key[len(API_KEY_PREFIX):]
    if len(random_part) != API_KEY_LENGTH:
        return False
    return all(c in string.ascii_letters + string.digits for c in random_part)


def sanitize_agent_name(name: str) -> str:
    """
    Sanitize agent name for storage.

    Args:
        name: Raw agent name

    Returns:
        str: Sanitized name
    """
    # Remove leading/trailing whitespace
    name = name.strip()
    # Limit length
    return name[:100]


def sanitize_email(email: str) -> str:
    """
    Sanitize and normalize email address.

    Args:
        email: Raw email address

    Returns:
        str: Normalized email (lowercase, trimmed)
    """
    return email.strip().lower()[:255]


# ============== Rate Limiting Key Generation ==============

def generate_rate_limit_key(identifier: str, action: str) -> str:
    """
    Generate a rate limiting key.

    Args:
        identifier: Client identifier (IP, API key prefix, etc.)
        action: Action being rate limited (register, claim, heartbeat)

    Returns:
        str: Rate limit key
    """
    return f"ratelimit:{action}:{identifier}"


# ============== Heartbeat Utilities ==============

def should_update_heartbeat(last_heartbeat: Optional[datetime],
                            min_interval_seconds: int = 60) -> bool:
    """
    Determine if heartbeat should be persisted to database.

    Used to reduce database writes by only updating periodically.

    Args:
        last_heartbeat: Last recorded heartbeat timestamp
        min_interval_seconds: Minimum seconds between DB updates

    Returns:
        bool: True if should update database
    """
    if last_heartbeat is None:
        return True
    elapsed = (datetime.utcnow() - last_heartbeat).total_seconds()
    return elapsed >= min_interval_seconds
