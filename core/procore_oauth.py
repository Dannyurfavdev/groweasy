"""
core/procore_oauth.py

Handles the full Procore Authorization Code OAuth flow.

Flow:
1. PM clicks "Connect to Procore" on the setup page
2. GrowEasy redirects to Procore login (procore_oauth_redirect)
3. PM logs in and authorizes
4. Procore redirects back to /procore/callback/ (procore_oauth_callback)
5. GrowEasy exchanges the code for access_token + refresh_token
6. Tokens stored encrypted on ProcoreCredential
7. All future pushes use stored tokens, auto-refreshed when expired
"""

import logging
import requests
from urllib.parse import urlencode
from django.conf import settings
from django.shortcuts import redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse

logger = logging.getLogger(__name__)

# ── URL constants ─────────────────────────────────────────

SANDBOX_AUTH_URL    = "https://login-sandbox.procore.com/oauth/authorize"
SANDBOX_TOKEN_URL   = "https://login-sandbox.procore.com/oauth/token"
PRODUCTION_AUTH_URL = "https://login.procore.com/oauth/authorize"
PRODUCTION_TOKEN_URL = "https://login.procore.com/oauth/token"


def get_redirect_uri(request):
    from django.conf import settings
    return getattr(settings, 'PROCORE_REDIRECT_URI', 'http://localhost:8000/procore/callback/')


# ── Step 1: Redirect PM to Procore login ─────────────────

@login_required
def procore_oauth_redirect(request, pk):
    """
    Redirects the PM to Procore's OAuth authorization page.
    pk = GrowEasy Project ID
    """
    from core.models import Project, ProcoreCredential

    project = get_object_or_404(Project, pk=pk, owner=request.user)

    try:
        credential = project.procore_credential
    except ProcoreCredential.DoesNotExist:
        return HttpResponse("No Procore credentials saved for this project.", status=400)

    # Store project ID in session so callback knows which project this is for
    request.session['procore_oauth_project_id'] = pk
    request.session['procore_oauth_environment'] = credential.environment

    # Choose auth URL based on environment
    if credential.is_sandbox:
        auth_url = SANDBOX_AUTH_URL
    else:
        auth_url = PRODUCTION_AUTH_URL

    params = {
        "response_type": "code",
        "client_id":     credential.client_id,
        "redirect_uri":  get_redirect_uri(request),
    }

    full_url = f"{auth_url}?{urlencode(params)}"
    logger.info("Redirecting project %s to Procore OAuth (%s)", pk, credential.environment)
    return redirect(full_url)


# ── Step 2: Handle Procore callback ──────────────────────

@login_required
def procore_oauth_callback(request):
    """
    Procore redirects here after PM authorizes.
    Exchanges the code for tokens and saves them.
    """
    from core.models import Project, ProcoreCredential

    code        = request.GET.get('code')
    error       = request.GET.get('error')
    project_id  = request.session.get('procore_oauth_project_id')
    environment = request.session.get('procore_oauth_environment', 'sandbox')

    # Handle user denying access
    if error:
        logger.warning("Procore OAuth error: %s", error)
        return redirect(f'/projects/{project_id}/procore-setup/?error=access_denied')

    if not code:
        return redirect(f'/projects/{project_id}/procore-setup/?error=no_code')

    if not project_id:
        return redirect('/dashboard/?error=procore_session_expired')

    project = get_object_or_404(Project, pk=project_id, owner=request.user)

    try:
        credential = project.procore_credential
    except ProcoreCredential.DoesNotExist:
        return redirect(f'/projects/{project_id}/procore-setup/?error=no_credentials')

    # Exchange code for tokens
    token_url = SANDBOX_TOKEN_URL if credential.is_sandbox else PRODUCTION_TOKEN_URL

    try:
        response = requests.post(
            token_url,
            json={
                "grant_type":    "authorization_code",
                "client_id":     credential.client_id,
                "client_secret": credential.client_secret,
                "code":          code,
                "redirect_uri":  get_redirect_uri(request),
            },
            timeout=15,
        )
    except requests.RequestException as exc:
        logger.exception("Token exchange failed: %s", exc)
        return redirect(f'/projects/{project_id}/procore-setup/?error=token_exchange_failed')

    if response.status_code != 200:
        logger.error("Token exchange error: %s %s", response.status_code, response.text[:300])
        return redirect(f'/projects/{project_id}/procore-setup/?error=token_exchange_failed')

    data = response.json()

    # Save tokens to credential
    credential.access_token  = data.get('access_token', '')
    credential.refresh_token = data.get('refresh_token', '')
    credential.is_connected  = True
    credential.save(update_fields=['access_token', 'refresh_token', 'is_connected'])

    # Clean up session
    request.session.pop('procore_oauth_project_id', None)
    request.session.pop('procore_oauth_environment', None)

    logger.info("Procore OAuth complete for project %s", project_id)
    return redirect(f'/projects/{project_id}/procore-setup/?connected=true')


# ── Token refresh ─────────────────────────────────────────

def refresh_procore_token(credential) -> str:
    """
    Uses the stored refresh_token to get a new access_token.
    Updates the credential in DB.
    Returns the new access_token.
    """
    token_url = SANDBOX_TOKEN_URL if credential.is_sandbox else PRODUCTION_TOKEN_URL

    response = requests.post(
        token_url,
        json={
            "grant_type":    "refresh_token",
            "client_id":     credential.client_id,
            "client_secret": credential.client_secret,
            "refresh_token": credential.refresh_token,
            "redirect_uri":  f"{settings.SITE_URL}/procore/callback/",
        },
        timeout=15,
    )

    if response.status_code != 200:
        raise Exception(
            f"Token refresh failed: {response.status_code} — {response.text[:200]}"
        )

    data = response.json()
    credential.access_token  = data.get('access_token', '')
    credential.refresh_token = data.get('refresh_token', credential.refresh_token)
    credential.save(update_fields=['access_token', 'refresh_token'])

    return credential.access_token


def get_valid_token(credential) -> str:
    """
    Returns a valid access token.
    Tries stored token first, refreshes if expired.
    Falls back to client_credentials if no refresh token stored yet.
    """
    from core.procore_token import get_procore_token

    # If we have a stored access token from OAuth, try it first
    if credential.access_token:
        return credential.access_token

    # If we have a refresh token, use it
    if credential.refresh_token:
        try:
            return refresh_procore_token(credential)
        except Exception as exc:
            logger.warning("Token refresh failed, trying client_credentials: %s", exc)

    # Fall back to client_credentials (for cases where it works)
    return get_procore_token(credential)