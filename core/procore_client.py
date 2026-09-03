# core/procore_client.py
#
# Handles all HTTP communication with Procore's REST API v1.0.
# Call send_project_export() with the dict from build_project_export()
# plus the customer's Procore credentials.

import requests

#PROCORE_BASE = "https://api.procore.com/rest/v1.0"

PROCORE_BASE = "https://sandbox.procore.com/rest/v1.0"

#PROCORE_BASE = "https://sandbox.procore.com/4282700/company/home"


# ─────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────

def _headers(access_token):
    """Auth header used by every JSON request."""
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type":  "application/json",
    }


def _post(url, payload, access_token):
    """
    POST a JSON payload. Returns (success: bool, response_data: dict).
    Never raises — caller decides what to do with failures.
    """
    try:
        r = requests.post(url, json=payload, headers=_headers(access_token), timeout=20)
        r.raise_for_status()
        return True, r.json()
    except requests.HTTPError as e:
        # Procore returns useful error detail in the response body
        try:
            detail = e.response.json()
        except Exception:
            detail = e.response.text
        print(f"[Procore] HTTP {e.response.status_code} → {url}\n  Detail: {detail}")
        return False, detail
    except requests.RequestException as e:
        print(f"[Procore] Network error → {url}\n  {e}")
        return False, {"error": str(e)}


# ─────────────────────────────────────────────
# 1. SEND DAILY LOG
# ─────────────────────────────────────────────

def send_daily_log(access_token, company_id, procore_project_id, daily_log_payload):
    """
    POST one daily log entry to Procore.

    daily_log_payload is the dict from build_daily_log() — the
    {"daily_log": {...}} shape, passed straight through.

    Returns (success: bool, procore_log_id: int or None)
    """
    url = f"{PROCORE_BASE}/projects/{procore_project_id}/daily_logs"

    # Procore also requires company_id as a query param on this endpoint
    url += f"?company_id={company_id}"

    success, data = _post(url, daily_log_payload, access_token)

    if success:
        log_id = data.get("id")
        print(f"[Procore] Daily log created → id={log_id}")
        return True, log_id

    return False, None


# ─────────────────────────────────────────────
# 2. SEND OBSERVATION
# ─────────────────────────────────────────────

def send_observation(access_token, company_id, procore_project_id, observation_payload):
    """
    POST one observation to Procore.

    observation_payload is the dict from build_observation() —
    the {"observation_item": {...}} shape.

    Returns (success: bool, procore_observation_id: int or None)
    """
    url = (
        f"{PROCORE_BASE}/projects/{procore_project_id}"
        f"/observations/items?company_id={company_id}"
    )

    success, data = _post(url, observation_payload, access_token)

    if success:
        obs_id = data.get("id")
        print(f"[Procore] Observation created → id={obs_id}")
        return True, obs_id

    return False, None


# ─────────────────────────────────────────────
# 3. SEND PHOTO
# ─────────────────────────────────────────────

def send_photo(access_token, company_id, procore_project_id, metadata, binary_content):
    """
    Upload one photo to Procore as multipart/form-data.

    metadata     — dict with 'description' and 'source' keys
    binary_content — raw bytes from build_photo_payload()

    Note: NO Content-Type header here — requests sets the
    multipart boundary automatically when you use 'files='.
    Manually setting it breaks the boundary string.

    Returns (success: bool, procore_image_id: int or None)
    """
    url = (
        f"{PROCORE_BASE}/projects/{procore_project_id}"
        f"/images?company_id={company_id}"
    )

    auth_header = {"Authorization": f"Bearer {access_token}"}

    try:
        r = requests.post(
            url,
            headers=auth_header,        # no Content-Type — let requests handle it
            files={"file": ("photo.jpg", binary_content, "image/jpeg")},
            data=metadata,              # description + source go as form fields
            timeout=60,                 # photos can be large, give them time
        )
        r.raise_for_status()
        image_id = r.json().get("id")
        print(f"[Procore] Photo uploaded → id={image_id}")
        return True, image_id

    except requests.HTTPError as e:
        try:
            detail = e.response.json()
        except Exception:
            detail = e.response.text
        print(f"[Procore] Photo upload failed HTTP {e.response.status_code}: {detail}")
        return False, None

    except requests.RequestException as e:
        print(f"[Procore] Photo upload network error: {e}")
        return False, None


# ─────────────────────────────────────────────
# 4. SEND FULL PROJECT EXPORT
# ─────────────────────────────────────────────

def send_project_export(export_data, access_token, company_id, procore_project_id):
    """
    Send a complete project export to Procore.

    export_data is the dict returned by build_project_export().
    Sends daily logs, observations, and photos in order.
    Collects a full result report — never stops on a single failure.

    Returns a result dict:
    {
        "logs_sent":         int,
        "logs_failed":       int,
        "observations_sent": int,
        "observations_failed": int,
        "photos_sent":       int,
        "photos_failed":     int,
        "procore_ids": {
            "daily_logs":   [list of Procore log IDs],
            "observations": [list of Procore observation IDs],
            "photos":       [list of Procore image IDs],
        }
    }
    """
    result = {
        "logs_sent":            0,
        "logs_failed":          0,
        "observations_sent":    0,
        "observations_failed":  0,
        "photos_sent":          0,
        "photos_failed":        0,
        "procore_ids": {
            "daily_logs":   [],
            "observations": [],
            "photos":       [],
        }
    }

    print(f"\n[Procore] Starting export for: {export_data['project_name']}")
    print(f"  → {len(export_data['daily_logs'])} daily logs")
    print(f"  → {len(export_data['observations'])} observations")
    print(f"  → {len(export_data['photos'])} photos")

    # ── Daily logs ────────────────────────────
    for payload in export_data["daily_logs"]:
        success, log_id = send_daily_log(
            access_token, company_id, procore_project_id, payload
        )
        if success:
            result["logs_sent"] += 1
            result["procore_ids"]["daily_logs"].append(log_id)
        else:
            result["logs_failed"] += 1

    # ── Observations ──────────────────────────
    for payload in export_data["observations"]:
        success, obs_id = send_observation(
            access_token, company_id, procore_project_id, payload
        )
        if success:
            result["observations_sent"] += 1
            result["procore_ids"]["observations"].append(obs_id)
        else:
            result["observations_failed"] += 1

    # ── Photos ───────────────────────────────
    # export_data["photos"] is a list of (metadata, binary, file_path) tuples
    for metadata, binary, file_path in export_data["photos"]:
        success, image_id = send_photo(
            access_token, company_id, procore_project_id, metadata, binary
        )
        if success:
            result["photos_sent"] += 1
            result["procore_ids"]["photos"].append(image_id)
        else:
            result["photos_failed"] += 1

    # ── Summary print ─────────────────────────
    print(f"\n[Procore] Export complete for: {export_data['project_name']}")
    print(f"  Logs:         {result['logs_sent']} sent, {result['logs_failed']} failed")
    print(f"  Observations: {result['observations_sent']} sent, {result['observations_failed']} failed")
    print(f"  Photos:       {result['photos_sent']} sent, {result['photos_failed']} failed")

    return result