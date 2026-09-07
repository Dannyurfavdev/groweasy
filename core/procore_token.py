"""
core/procore_token.py

Fetches a fresh Procore OAuth token using client_credentials grant.
Called automatically before every push — PM never touches tokens.
"""

import logging
import requests

logger = logging.getLogger(__name__)


def get_procore_token(credential) -> str:
    """
    Fetches a fresh access token from Procore using the stored
    client_id and client_secret on a ProcoreCredential instance.

    Returns the access token string.
    Raises Exception on failure with a human-readable message.

    Token lasts 2 hours — we fetch fresh on every push so expiry
    is never a problem.
    """
    try:
        response = requests.post(
            credential.token_url,
            json={
                "grant_type":    "client_credentials",
                "client_id":     credential.client_id,
                "client_secret": credential.client_secret,
            },
            timeout=15,
        )
    except requests.Timeout:
        raise Exception(
            "Procore token request timed out. Check your connection."
        )
    except requests.ConnectionError:
        raise Exception(
            "Could not reach Procore authentication server. "
            "Check your internet connection."
        )

    if response.status_code == 401:
        raise Exception(
            "Invalid Client ID or Client Secret. "
            "Check your Procore credentials."
        )

    if response.status_code != 200:
        raise Exception(
            f"Procore returned {response.status_code} when fetching token. "
            f"Response: {response.text[:200]}"
        )

    data = response.json()
    token = data.get("access_token")

    if not token:
        raise Exception(
            "Procore response did not include an access token. "
            f"Response: {str(data)[:200]}"
        )

    logger.info(
        "Procore token fetched for project %s (%s environment)",
        credential.project_id,
        credential.environment,
    )

    return token