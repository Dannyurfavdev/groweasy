"""
core/procore_client.py — Final version

Reads active mode from ProcoreMode (DB) and credentials from
ProcoreCredential (per project). No manual token entry needed.
Supports Mock / Sandbox / Production via the dev tools panel.
"""

import requests
import logging

logger = logging.getLogger(__name__)


def _get_base_url(credential=None) -> str:
    """
    Returns the correct API base URL for the current mode.
    Reads from ProcoreMode singleton in DB — no restart needed.
    """
    from core.models import ProcoreMode
    mode = ProcoreMode.get()
    return mode.get_base_url(credential)


def _get_access_token(credential) -> str:
    """
    Returns a valid access token for the given credential.
    Uses OAuth token if connected, falls back to client_credentials.
    """
    from core.models import ProcoreMode
    from core.procore_oauth import get_valid_token

    mode = ProcoreMode.get()

    # Mock mode — use a dummy token, mock endpoints don't validate
    if mode.is_mock:
        return "mock-token"

    return get_valid_token(credential)


def _headers(access_token: str, company_id: str) -> dict:
    return {
        "Authorization":      f"Bearer {access_token}",
        "Content-Type":       "application/json",
        "Procore-Company-Id": str(company_id),
    }


def _post(url, payload, access_token, company_id):
    """
    POST a JSON payload. Returns (success: bool, response_data: dict).
    Never raises — caller decides what to do with failures.
    """
    try:
        r = requests.post(
            url,
            json=payload,
            headers=_headers(access_token, company_id),
            timeout=20,
        )
        r.raise_for_status()
        return True, r.json()
    except requests.HTTPError as e:
        try:
            detail = e.response.json()
        except Exception:
            detail = e.response.text
        logger.error("Procore HTTP %s → %s | %s", e.response.status_code, url, detail)
        return False, detail
    except requests.RequestException as e:
        logger.error("Procore network error → %s | %s", url, e)
        return False, {"error": str(e)}


# ─────────────────────────────────────────────
# MOCK HANDLERS
# ─────────────────────────────────────────────

def _mock_post(path: str, payload: dict, mock_base_url: str) -> tuple[bool, dict]:
    """
    Posts to the local mock endpoints.
    mock_base_url = http://localhost:8000/mock-procore
    path = /rest/v1.0/projects/.../daily_logs
    """
    import json as json_lib
    url = f"{mock_base_url}/rest/v1.0{path}"
    try:
        r = requests.post(
            url,
            json=payload,
            headers={"Authorization": "Bearer mock-token", "Content-Type": "application/json"},
            timeout=10,
        )
        r.raise_for_status()
        return True, r.json()
    except requests.HTTPError as e:
        logger.error("Mock Procore HTTP %s → %s", e.response.status_code, url)
        return False, {"error": e.response.text}
    except requests.RequestException as e:
        logger.error("Mock Procore network error → %s | %s", url, e)
        return False, {"error": str(e)}


# ─────────────────────────────────────────────
# 1. SEND DAILY LOG
# ─────────────────────────────────────────────

def send_daily_log(credential, daily_log_payload):
    """
    POST one daily log entry to Procore (or mock).
    Returns (success: bool, procore_log_id: int or None)
    """
    from core.models import ProcoreMode
    mode = ProcoreMode.get()
    project_id = credential.procore_project_id
    company_id = credential.company_id

    if mode.is_mock:
        success, data = _mock_post(
            f"/projects/{project_id}/daily_logs",
            daily_log_payload,
            mode.mock_base_url,
        )
    else:
        token = _get_access_token(credential)
        base  = _get_base_url(credential)
        url   = f"{base}/projects/{project_id}/daily_logs?company_id={company_id}"
        success, data = _post(url, daily_log_payload, token, company_id)

    if success:
        log_id = data.get("id")
        logger.info("Procore daily log created → id=%s (mode=%s)", log_id, mode.mode)
        return True, log_id

    return False, None


# ─────────────────────────────────────────────
# 2. SEND OBSERVATION
# ─────────────────────────────────────────────

def send_observation(credential, observation_payload):
    """
    POST one observation to Procore (or mock).
    Returns (success: bool, procore_observation_id: int or None)
    """
    from core.models import ProcoreMode
    mode = ProcoreMode.get()
    project_id = credential.procore_project_id
    company_id = credential.company_id

    if mode.is_mock:
        success, data = _mock_post(
            f"/projects/{project_id}/observations/items",
            observation_payload,
            mode.mock_base_url,
        )
    else:
        token = _get_access_token(credential)
        base  = _get_base_url(credential)
        url   = f"{base}/projects/{project_id}/observations/items?company_id={company_id}"
        success, data = _post(url, observation_payload, token, company_id)

    if success:
        obs_id = data.get("id")
        logger.info("Procore observation created → id=%s (mode=%s)", obs_id, mode.mode)
        return True, obs_id

    return False, None


# ─────────────────────────────────────────────
# 3. SEND PHOTO
# ─────────────────────────────────────────────

def send_photo(credential, metadata, binary_content):
    """
    Upload one photo to Procore (or mock) as multipart/form-data.
    Returns (success: bool, procore_image_id: int or None)
    """
    from core.models import ProcoreMode
    mode = ProcoreMode.get()
    project_id = credential.procore_project_id
    company_id = credential.company_id

    if mode.is_mock:
        # Mock: just POST the metadata as JSON (no binary in mock)
        success, data = _mock_post(
            f"/projects/{project_id}/images",
            {"image": metadata},
            mode.mock_base_url,
        )
        if success:
            image_id = data.get("id")
            logger.info("Mock photo uploaded → id=%s", image_id)
            return True, image_id
        return False, None

    # Real Procore — multipart upload
    token = _get_access_token(credential)
    base  = _get_base_url(credential)
    url   = f"{base}/projects/{project_id}/images?company_id={company_id}"

    try:
        r = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}",
                     "Procore-Company-Id": str(company_id)},
            files={"file": ("photo.jpg", binary_content, "image/jpeg")},
            data=metadata,
            timeout=60,
        )
        r.raise_for_status()
        image_id = r.json().get("id")
        logger.info("Procore photo uploaded → id=%s (mode=%s)", image_id, mode.mode)
        return True, image_id

    except requests.HTTPError as e:
        try:
            detail = e.response.json()
        except Exception:
            detail = e.response.text
        logger.error("Photo upload failed HTTP %s: %s", e.response.status_code, detail)
        return False, None
    except requests.RequestException as e:
        logger.error("Photo upload network error: %s", e)
        return False, None


# ─────────────────────────────────────────────
# 4. SEND FULL PROJECT EXPORT
# ─────────────────────────────────────────────

def send_project_export(export_data, credential):
    """
    Send a complete project export to Procore (or mock).

    credential: ProcoreCredential instance for the project.
    export_data: dict from build_project_export().

    Returns result dict with counts and Procore IDs.
    """
    from core.models import ProcoreMode
    mode = ProcoreMode.get()

    result = {
        "logs_sent":             0,
        "logs_failed":           0,
        "observations_sent":     0,
        "observations_failed":   0,
        "photos_sent":           0,
        "photos_failed":         0,
        "mode":                  mode.mode,
        "procore_ids": {
            "daily_logs":   [],
            "observations": [],
            "photos":       [],
        }
    }

    logger.info(
        "Starting export for %s | mode=%s | logs=%s obs=%s photos=%s",
        export_data["project_name"],
        mode.mode,
        len(export_data["daily_logs"]),
        len(export_data["observations"]),
        len(export_data["photos"]),
    )

    # ── Daily logs ──────────────────────────────────
    for payload in export_data["daily_logs"]:
        success, log_id = send_daily_log(credential, payload)
        if success:
            result["logs_sent"] += 1
            result["procore_ids"]["daily_logs"].append(log_id)
        else:
            result["logs_failed"] += 1

    # ── Observations ─────────────────────────────────
    for payload in export_data["observations"]:
        success, obs_id = send_observation(credential, payload)
        if success:
            result["observations_sent"] += 1
            result["procore_ids"]["observations"].append(obs_id)
        else:
            result["observations_failed"] += 1

    # ── Photos ───────────────────────────────────────
    # export_data["photos"] is a list of (metadata, binary, file_path) tuples
    for metadata, binary, file_path in export_data["photos"]:
        success, image_id = send_photo(credential, metadata, binary)
        if success:
            result["photos_sent"] += 1
            result["procore_ids"]["photos"].append(image_id)
        else:
            result["photos_failed"] += 1

    logger.info(
        "Export complete for %s | logs: %s/%s | obs: %s/%s | photos: %s/%s",
        export_data["project_name"],
        result["logs_sent"],    result["logs_sent"]    + result["logs_failed"],
        result["observations_sent"], result["observations_sent"] + result["observations_failed"],
        result["photos_sent"],  result["photos_sent"]  + result["photos_failed"],
    )

    return result