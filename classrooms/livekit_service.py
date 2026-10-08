"""
LiveKit WebRTC token generation and room service for TeachLive.
Generates cryptographically secure, short-lived JWT access tokens with
strict server-side role permissions (teacher can publish, student can only subscribe).
Never exposes API secrets to client-side code.
"""

from datetime import timedelta
import logging
import os
import secrets

from django.conf import settings
from livekit import api

logger = logging.getLogger(__name__)


class LiveKitError(Exception):
    """Base exception for LiveKit operations."""
    pass


class LiveKitNotConfiguredError(LiveKitError):
    """Raised when LiveKit environment variables are missing."""
    pass


def get_livekit_url() -> str:
    """Retrieves LiveKit WebRTC server WebSocket URL (e.g. wss://example.livekit.cloud)."""
    return os.getenv('LIVEKIT_URL', '').strip()


def get_livekit_api_key() -> str:
    """Retrieves LiveKit API Key from environment."""
    return os.getenv('LIVEKIT_API_KEY', '').strip()


def get_livekit_api_secret() -> str:
    """Retrieves LiveKit API Secret from environment."""
    return os.getenv('LIVEKIT_API_SECRET', '').strip()


def is_livekit_configured() -> bool:
    """Checks whether all required LiveKit credentials are set in environment."""
    return bool(get_livekit_url() and get_livekit_api_key() and get_livekit_api_secret())


def get_livekit_room_name(live_class) -> str:
    """
    Computes standard LiveKit room name.
    Prefixes with 'teachlive-' followed by clean room_code.
    """
    code = live_class.room_code.strip()
    return f"teachlive-{code}"


def generate_livekit_access_token(live_class, user, is_teacher: bool) -> dict:
    """
    Generates a secure, short-lived LiveKit access token.
    Enforces strict role permissions:
    - Teacher: can_publish=True, can_subscribe=True, can_publish_data=True, room_admin=True
    - Student: can_publish=False, can_subscribe=True, can_publish_data=True (chat only)

    Returns a dictionary suitable for frontend JSON consumption without sensitive secrets.
    """
    if not is_livekit_configured():
        raise LiveKitNotConfiguredError(
            "LiveKit credentials (LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET) "
            "are not configured. Please set them in your .env file."
        )

    api_key = get_livekit_api_key()
    api_secret = get_livekit_api_secret()
    livekit_url = get_livekit_url()
    room_name = get_livekit_room_name(live_class)

    # Unique identity and display name
    unique_suffix = secrets.token_hex(3)
    user_role_str = "teacher" if is_teacher else "student"
    identity = f"{user_role_str}_{user.id}_{unique_suffix}"
    display_name = user.get_full_name() or user.username

    # Define video grants server-side
    if is_teacher:
        grants = api.VideoGrants(
            room_join=True,
            room=room_name,
            can_publish=True,
            can_subscribe=True,
            can_publish_data=True,
            room_admin=True,
        )
    else:
        # Student permissions: strictly subscribe-only for audio/video/screen
        grants = api.VideoGrants(
            room_join=True,
            room=room_name,
            can_publish=False,
            can_subscribe=True,
            can_publish_data=True,
            room_admin=False,
        )

    # Token with 2 hours validity
    token = (
        api.AccessToken(api_key, api_secret)
        .with_identity(identity)
        .with_name(display_name)
        .with_grants(grants)
        .with_ttl(timedelta(hours=2))
        .to_jwt()
    )

    return {
        'token': token,
        'url': livekit_url,
        'livekit_url': livekit_url,
        'room_name': room_name,
        'identity': identity,
        'display_name': display_name,
        'is_teacher': is_teacher,
        'can_publish': is_teacher,
    }

