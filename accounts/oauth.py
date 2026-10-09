"""
Google OAuth 2.0 / OpenID Connect service for TeachLive student authentication.
Implements secure authorization URL generation, state validation (anti-CSRF),
code exchange, userinfo fetching, account conflict validation, and idempotent student creation.
"""

import logging
import os
import random
import re
import secrets
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.urls import reverse
from django.utils import timezone
import requests

logger = logging.getLogger(__name__)
User = get_user_model()

GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v3/userinfo"


# =====================================================================
# Custom Exceptions for Robust Error Handling
# =====================================================================

class GoogleOAuthException(Exception):
    """Base exception for Google OAuth errors."""
    pass


class GoogleAuthCancelledError(GoogleOAuthException):
    """Raised when Google sign-in is cancelled by user."""
    pass


class OAuthFailureError(GoogleOAuthException):
    """Raised when token exchange or communication fails."""
    pass


class InvalidGoogleResponseError(GoogleOAuthException):
    """Raised when Google response is malformed or missing required fields."""
    pass


class AccountConflictError(GoogleOAuthException):
    """Raised when an existing account has a conflicting role (e.g. Teacher/Admin)."""
    pass


# =====================================================================
# Configuration & URL Generation
# =====================================================================

def get_google_client_id() -> str:
    """Retrieves Google Client ID from environment variables."""
    return os.getenv('GOOGLE_CLIENT_ID', '').strip()


def get_google_client_secret() -> str:
    """Retrieves Google Client Secret from environment variables."""
    return os.getenv('GOOGLE_CLIENT_SECRET', '').strip()


def is_google_oauth_configured() -> bool:
    """Returns True if Google OAuth credentials are fully provided in environment."""
    return bool(get_google_client_id() and get_google_client_secret())


def build_redirect_uri(request) -> str:
    """Builds the fully qualified callback URL for Google OAuth."""
    custom_uri = os.getenv('GOOGLE_REDIRECT_URI', '').strip()
    if custom_uri:
        return custom_uri
    try:
        uri = request.build_absolute_uri(reverse('student:google_callback'))
    except Exception:
        uri = request.build_absolute_uri('/student/google/callback/')
    if not settings.DEBUG and uri.startswith('http://') and not uri.startswith('http://localhost') and not uri.startswith('http://127.0.0.1'):
        uri = 'https://' + uri[7:]
    return uri


def generate_google_auth_url(request, next_url: str = '') -> str:
    """
    Generates a secure Google OAuth 2.0 authorization URL.
    Generates an anti-CSRF state token and preserves the destination `next_url`.
    """
    state = secrets.token_urlsafe(32)
    request.session['google_oauth_state'] = state
    if next_url:
        request.session['google_oauth_next'] = next_url

    client_id = get_google_client_id()
    redirect_uri = build_redirect_uri(request)

    params = {
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': 'openid email profile',
        'state': state,
        'access_type': 'online',
        'prompt': 'select_account',
    }
    return f"{GOOGLE_AUTH_ENDPOINT}?{urlencode(params)}"


# =====================================================================
# Code Exchange & User Information
# =====================================================================

def exchange_code_for_user_info(request, code: str) -> dict:
    """
    Exchanges authorization code for tokens and fetches userinfo from Google.
    Returns userinfo dict containing 'sub', 'email', 'name', 'picture', etc.
    """
    if not code:
        raise OAuthFailureError("Missing authorization code from Google redirect.")

    redirect_uri = build_redirect_uri(request)
    client_id = get_google_client_id()
    client_secret = get_google_client_secret()

    payload = {
        'code': code,
        'client_id': client_id,
        'client_secret': client_secret,
        'redirect_uri': redirect_uri,
        'grant_type': 'authorization_code',
    }

    try:
        token_response = requests.post(
            GOOGLE_TOKEN_ENDPOINT,
            data=payload,
            timeout=10,
            headers={'Accept': 'application/json'}
        )
    except requests.RequestException as e:
        logger.error("Network error during Google token exchange: %s", e)
        raise OAuthFailureError("Network error while connecting to Google servers.")

    if not token_response.ok:
        logger.error("Google token exchange failed: %s", token_response.text)
        raise OAuthFailureError("Failed to verify authentication with Google. Code may have expired.")

    token_data = token_response.json()
    access_token = token_data.get('access_token')
    if not access_token:
        raise InvalidGoogleResponseError("Google token response missing access token.")

    try:
        userinfo_response = requests.get(
            GOOGLE_USERINFO_ENDPOINT,
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=10
        )
    except requests.RequestException as e:
        logger.error("Network error fetching Google userinfo: %s", e)
        raise OAuthFailureError("Network error while retrieving Google profile.")

    if not userinfo_response.ok:
        logger.error("Google userinfo fetch failed: %s", userinfo_response.text)
        raise InvalidGoogleResponseError("Could not retrieve user profile from Google.")

    user_info = userinfo_response.json()
    if not user_info.get('sub') and not user_info.get('email'):
        raise InvalidGoogleResponseError("Google profile response missing required identity fields.")

    return user_info


def generate_unique_username(email: str, name: str = '') -> str:
    """Generates a clean, alphanumeric unique username for a new student."""
    base = ''
    if email:
        base = email.split('@')[0]
    elif name:
        base = re.sub(r'[^a-zA-Z0-9]', '_', name.lower())
    else:
        base = 'student'

    base = re.sub(r'[^a-zA-Z0-9_]', '', base)[:20]
    if not base:
        base = 'student'

    username = base
    counter = 1
    while User.objects.filter(username=username).exists():
        rand_suffix = random.randint(100, 9999)
        username = f"{base[:15]}_{rand_suffix}"
        counter += 1
        if counter > 50:
            username = f"{base[:10]}_{secrets.token_hex(4)}"
            break

    return username


# =====================================================================
# Student Creation & Idempotent Login
# =====================================================================

def get_or_create_student_from_google(user_info: dict) -> tuple[User, bool]:
    """
    Finds or creates a student account using Google user profile data.
    Ensures:
    - No duplicate accounts for the same email
    - Role is set to STUDENT
    - Existing teacher or admin accounts trigger AccountConflictError
    - Name, email, picture, google_id, and last_login are stored/updated
    - Password is non-usable (OAuth managed)
    Returns: (user, created: bool)
    """
    google_id = str(user_info.get('sub', '')).strip()
    email = str(user_info.get('email', '')).strip().lower()
    name = str(user_info.get('name', '')).strip()
    given_name = str(user_info.get('given_name', '')).strip()
    family_name = str(user_info.get('family_name', '')).strip()
    picture = str(user_info.get('picture', '')).strip()

    if not email and not google_id:
        raise InvalidGoogleResponseError("Google account did not return a valid email or ID.")

    # 1. Search for existing account by Google ID or by Email
    user = None
    if google_id:
        user = User.objects.filter(google_id=google_id).first()

    if not user and email:
        user = User.objects.filter(email__iexact=email).first()

    # 2. Account Conflict Check: Check if user exists and is a Teacher or Admin
    if user and (user.is_teacher or user.is_admin_role or user.role in (User.Role.TEACHER, User.Role.ADMIN)):
        raise AccountConflictError(
            f"The email '{email}' is registered as an Instructor/Administrator account. "
            "Please sign in via the Teacher or Admin Portal."
        )

    created = False
    if user:
        # Existing student account found - update Google ID and profile details
        updated_fields = []
        if google_id and user.google_id != google_id:
            user.google_id = google_id
            updated_fields.append('google_id')

        if picture and user.google_picture_url != picture:
            user.google_picture_url = picture
            updated_fields.append('google_picture_url')

        if not user.first_name and (given_name or name):
            user.first_name = given_name or (name.split()[0] if name else '')
            updated_fields.append('first_name')

        if not user.last_name and family_name:
            user.last_name = family_name
            updated_fields.append('last_name')

        if user.role != User.Role.STUDENT:
            user.role = User.Role.STUDENT
            updated_fields.append('role')

        user.last_login = timezone.now()
        updated_fields.append('last_login')
        user.save(update_fields=updated_fields)

    else:
        # Create brand new student account
        created = True
        username = generate_unique_username(email, name)
        first_name = given_name or (name.split()[0] if name else 'Student')
        last_name = family_name or (' '.join(name.split()[1:]) if name and len(name.split()) > 1 else '')

        user = User(
            username=username,
            email=email,
            first_name=first_name,
            last_name=last_name,
            role=User.Role.STUDENT,
            google_id=google_id,
            google_picture_url=picture,
            last_login=timezone.now(),
        )
        user.set_unusable_password()
        user.save()

    return user, created
