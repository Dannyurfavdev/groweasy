"""
core/procore_mock_views.py

Mock Procore API endpoints for local development and testing.
Mimics the real Procore sandbox API responses exactly so the
push flow works end-to-end without a real Procore account.

Add to core/urls.py ONLY in development:

    from django.conf import settings
    from core.procore_mock_views import (
        mock_procore_me,
        mock_procore_project,
        mock_procore_meeting,
        mock_procore_action_item,
    )

    if settings.DEBUG:
        urlpatterns += [
            path("mock-procore/rest/v1.0/me",
                 mock_procore_me,           name="mock_procore_me"),
            path("mock-procore/rest/v1.0/projects/<str:project_id>",
                 mock_procore_project,      name="mock_procore_project"),
            path("mock-procore/rest/v1.0/projects/<str:project_id>/meetings",
                 mock_procore_meeting,      name="mock_procore_meeting"),
            path("mock-procore/rest/v1.0/projects/<str:project_id>/meetings/<str:meeting_id>/meeting_action_items",
                 mock_procore_action_item,  name="mock_procore_action_item"),
        ]

Then in settings.py add:
    PROCORE_MOCK_BASE_URL = "http://localhost:8000/mock-procore"

And in core/procore_pusher.py, update ProcoreMeetingPusher.__init__:
    self.base_url = getattr(settings, 'PROCORE_MOCK_BASE_URL', None) or credential.api_base_url
"""

import json
import uuid
import logging
from datetime import datetime
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

logger = logging.getLogger(__name__)

# In-memory store — resets on server restart, fine for testing
_mock_store = {
    "meetings":     {},
    "action_items": {},
    "daily_logs":   {},
    "observations": {},
    "images":       {},
}


def _check_auth(request) -> tuple[bool, str]:
    """Validates the Bearer token is present."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False, "Missing or invalid Authorization header"
    token = auth[7:]
    if not token:
        return False, "Empty token"
    return True, ""


def _log_request(name, request):
    """Logs incoming mock API calls for visibility."""
    logger.info(
        "MOCK PROCORE [%s] %s %s | body: %s",
        name,
        request.method,
        request.path,
        request.body[:200] if request.body else "(empty)",
    )


# ── GET /me ──────────────────────────────────────────────

@csrf_exempt
@require_http_methods(["GET"])
def mock_procore_me(request):
    _log_request("me", request)
    ok, err = _check_auth(request)
    if not ok:
        return JsonResponse({"error": "Invalid Token"}, status=401)

    return JsonResponse({
        "id":    999999,
        "login": "test@groweasy.com",
        "name":  "GrowEasy Test User",
    })


# ── GET /projects/{id} ───────────────────────────────────

@csrf_exempt
@require_http_methods(["GET"])
def mock_procore_project(request, project_id):
    _log_request("project", request)
    ok, err = _check_auth(request)
    if not ok:
        return JsonResponse({"error": "Invalid Token"}, status=401)

    return JsonResponse({
        "id":          int(project_id),
        "name":        "Mock Sandbox Project",
        "description": "GrowEasy mock project for testing",
        "status":      "Active",
        "company": {
            "id":   4289633,
            "name": "GrowEasy Analytics",
        },
    })


# ── POST /projects/{id}/meetings ─────────────────────────

@csrf_exempt
@require_http_methods(["POST"])
def mock_procore_meeting(request, project_id):
    _log_request("meeting", request)
    ok, err = _check_auth(request)
    if not ok:
        return JsonResponse({"error": "Invalid Token"}, status=401)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    meeting_data = body.get("meeting", {})
    meeting_id   = str(uuid.uuid4().int)[:8]  # Short numeric-ish ID

    record = {
        "id":          meeting_id,
        "title":       meeting_data.get("title", "Untitled Meeting"),
        "date":        meeting_data.get("date"),
        "description": meeting_data.get("description", ""),
        "status":      meeting_data.get("status", "draft"),
        "project_id":  project_id,
        "created_at":  datetime.utcnow().isoformat(),
        "action_items": [],
    }

    _mock_store["meetings"][meeting_id] = record

    logger.info("MOCK PROCORE: Created meeting id=%s title='%s'",
                meeting_id, record["title"])

    return JsonResponse(record, status=201)


# ── POST /projects/{id}/meetings/{id}/meeting_action_items

@csrf_exempt
@require_http_methods(["POST"])
def mock_procore_action_item(request, project_id, meeting_id):
    _log_request("action_item", request)
    ok, err = _check_auth(request)
    if not ok:
        return JsonResponse({"error": "Invalid Token"}, status=401)

    if meeting_id not in _mock_store["meetings"]:
        return JsonResponse(
            {"error": f"Meeting {meeting_id} not found"},
            status=404,
        )

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    item_data = body.get("meeting_action_item", {})
    item_id   = str(uuid.uuid4().int)[:8]

    record = {
        "id":          item_id,
        "title":       item_data.get("title", ""),
        "description": item_data.get("description", ""),
        "assignee":    item_data.get("assignee"),
        "due_date":    item_data.get("due_date"),
        "status":      item_data.get("status", "initiated"),
        "meeting_id":  meeting_id,
        "project_id":  project_id,
        "created_at":  datetime.utcnow().isoformat(),
    }

    _mock_store["action_items"][item_id] = record
    _mock_store["meetings"][meeting_id]["action_items"].append(item_id)

    logger.info(
        "MOCK PROCORE: Created action item id=%s title='%s' assignee='%s'",
        item_id, record["title"], record["assignee"],
    )

    return JsonResponse(record, status=201)


# ── GET /mock-procore/store — debug view ─────────────────

@csrf_exempt
@require_http_methods(["GET"])
def mock_procore_store(request):
    """
    Debug endpoint — shows everything stored in the mock.
    Visit http://localhost:8000/mock-procore/store/ to inspect.
    """
    return JsonResponse({
        "meetings":     _mock_store.get("meetings", {}),
        "action_items": _mock_store.get("action_items", {}),
        "daily_logs":   _mock_store.get("daily_logs", {}),
        "observations": _mock_store.get("observations", {}),
        "images":       _mock_store.get("images", {}),
        "summary": {
            "total_meetings":     len(_mock_store.get("meetings", {})),
            "total_action_items": len(_mock_store.get("action_items", {})),
            "total_daily_logs":   len(_mock_store.get("daily_logs", {})),
            "total_observations": len(_mock_store.get("observations", {})),
            "total_images":       len(_mock_store.get("images", {})),
        }
    }, json_dumps_params={"indent": 2})


# ── POST /projects/{id}/daily_logs ───────────────────────

@csrf_exempt
@require_http_methods(["POST"])
def mock_procore_daily_log(request, project_id):
    _log_request("daily_log", request)
    ok, err = _check_auth(request)
    if not ok:
        return JsonResponse({"error": "Invalid Token"}, status=401)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    log_data = body.get("daily_log", {})
    log_id   = str(uuid.uuid4().int)[:8]

    record = {
        "id":         log_id,
        "date":       log_data.get("date"),
        "notes":      log_data.get("notes", ""),
        "weather":    log_data.get("weather", "Clear"),
        "project_id": project_id,
        "created_at": datetime.utcnow().isoformat(),
    }

    _mock_store.setdefault("daily_logs", {})[log_id] = record
    logger.info("MOCK PROCORE: Daily log id=%s date=%s", log_id, record["date"])
    return JsonResponse(record, status=201)


# ── POST /projects/{id}/observations/items ───────────────

@csrf_exempt
@require_http_methods(["POST"])
def mock_procore_observation(request, project_id):
    _log_request("observation", request)
    ok, err = _check_auth(request)
    if not ok:
        return JsonResponse({"error": "Invalid Token"}, status=401)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    obs_data = body.get("observation_item", {})
    obs_id   = str(uuid.uuid4().int)[:8]

    record = {
        "id":          obs_id,
        "title":       obs_data.get("title", ""),
        "description": obs_data.get("description", ""),
        "status":      obs_data.get("status", "open"),
        "type":        obs_data.get("type", "delay"),
        "project_id":  project_id,
        "created_at":  datetime.utcnow().isoformat(),
    }

    _mock_store.setdefault("observations", {})[obs_id] = record
    logger.info("MOCK PROCORE: Observation id=%s title=%s", obs_id, record["title"])
    return JsonResponse(record, status=201)


# ── POST /projects/{id}/images ───────────────────────────

@csrf_exempt
@require_http_methods(["POST"])
def mock_procore_image(request, project_id):
    _log_request("image", request)
    ok, err = _check_auth(request)
    if not ok:
        return JsonResponse({"error": "Invalid Token"}, status=401)

    image_id = str(uuid.uuid4().int)[:8]

    record = {
        "id":          image_id,
        "description": request.POST.get("description", "") or
                       (json.loads(request.body) if request.body else {}).get("image", {}).get("description", ""),
        "project_id":  project_id,
        "created_at":  datetime.utcnow().isoformat(),
    }

    _mock_store.setdefault("images", {})[image_id] = record
    logger.info("MOCK PROCORE: Image id=%s", image_id)
    return JsonResponse(record, status=201)