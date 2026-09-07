"""
Final core/procore_pusher.py
Replace your existing file entirely with this.
"""

import logging
import requests
from datetime import date

logger = logging.getLogger(__name__)

MOCK_API_PREFIX = "/rest/v1.0"


def dry_run_summary(meeting) -> dict:
    action_items = list(meeting.action_items.all().select_related("owner"))
    decisions    = list(meeting.decisions.all())
    blockers     = list(meeting.blockers.all())

    assigned   = [i for i in action_items if i.owner or i.owner_raw_name]
    unassigned = [i for i in action_items if not i.owner and not i.owner_raw_name]

    return {
        "meeting_title":     meeting.title or "Untitled Meeting",
        "meeting_date":      meeting.meeting_date.isoformat() if meeting.meeting_date else None,
        "action_item_count": len(action_items),
        "assigned_count":    len(assigned),
        "unassigned_count":  len(unassigned),
        "decision_count":    len(decisions),
        "blocker_count":     len(blockers),
        "action_items": [
            {
                "id":            i.id,
                "task":          i.task_description,
                "owner_name":    i.owner.contact_name if i.owner else (i.owner_raw_name or ""),
                "due_date":      i.due_date.isoformat() if i.due_date else None,
                "is_unassigned": not i.owner and not i.owner_raw_name,
            }
            for i in action_items
        ],
        "decisions": [{"description": d.description} for d in decisions],
        "blockers":  [{"description": b.description, "severity": b.severity} for b in blockers],
    }


class ProcoreMeetingPusher:

    def __init__(self, access_token: str, credential):
        from core.models import ProcoreMode

        self.access_token       = access_token
        self.credential         = credential
        self.company_id         = credential.company_id
        self.procore_project_id = credential.procore_project_id

        # Read active mode from DB — no restart needed
        self.procore_mode = ProcoreMode.get()
        self.is_mock      = self.procore_mode.is_mock
        self.base_url     = self.procore_mode.get_base_url(credential)

        self.headers = {
            "Authorization":      f"Bearer {access_token}",
            "Content-Type":       "application/json",
            "Procore-Company-Id": str(credential.company_id),
        }

        logger.info(
            "ProcoreMeetingPusher → %s | base: %s",
            self.procore_mode.mode.upper(),
            self.base_url,
        )

    def _url(self, path: str) -> str:
        """
        Builds full URL.
        Mock endpoints include /rest/v1.0/ prefix in their path.
        Real Procore endpoints use the base URL which already ends at /rest/v1.0.
        """
        if self.is_mock:
            # base_url = http://localhost:8000/mock-procore
            # path     = /projects/365573/meetings
            # result   = http://localhost:8000/mock-procore/rest/v1.0/projects/365573/meetings
            return f"{self.base_url}{MOCK_API_PREFIX}{path}"
        else:
            # base_url = https://sandbox.procore.com/rest/v1.0
            # path     = /projects/365573/meetings
            # result   = https://sandbox.procore.com/rest/v1.0/projects/365573/meetings
            return f"{self.base_url}{path}"

    def verify_credentials(self) -> tuple[bool, str]:
        me_url = self._url("/me")
        try:
            r = requests.get(me_url, headers=self.headers, timeout=10)
            if r.status_code == 401:
                return False, "Access token is invalid or expired."
            if r.status_code not in (200, 201):
                return False, f"Procore returned {r.status_code}: {r.text[:200]}"
        except requests.Timeout:
            return False, "Procore API timed out. Try again."
        except requests.ConnectionError:
            return False, "Could not reach Procore API. Check your connection."

        project_url = self._url(f"/projects/{self.procore_project_id}")
        try:
            r = requests.get(
                project_url,
                headers=self.headers,
                params={"company_id": self.company_id},
                timeout=10,
            )
            if r.status_code == 200:
                return True, ""
            elif r.status_code == 403:
                return False, "Access denied. Check your Procore permissions."
            elif r.status_code == 404:
                return False, f"Project ID {self.procore_project_id} not found."
            else:
                return False, f"Procore returned {r.status_code}: {r.text[:200]}"
        except requests.Timeout:
            return False, "Procore API timed out on project check."
        except requests.ConnectionError:
            return False, "Could not reach Procore API."

    def push_meeting(self, meeting) -> dict:
        url = self._url(f"/projects/{self.procore_project_id}/meetings")

        meeting_date = (
            meeting.meeting_date.isoformat()
            if meeting.meeting_date
            else date.today().isoformat()
        )

        notes_parts = []
        decisions = list(meeting.decisions.all())
        if decisions:
            notes_parts.append("=== DECISIONS ===")
            for d in decisions:
                made_by = d.made_by.contact_name if d.made_by else (d.made_by_raw_name or "")
                line = f"• {d.description}"
                if made_by:
                    line += f" ({made_by})"
                notes_parts.append(line)

        blockers = list(meeting.blockers.all())
        if blockers:
            notes_parts.append("\n=== BLOCKERS ===")
            for b in blockers:
                notes_parts.append(f"• [{b.severity}] {b.description}")
                if b.blocking_who:
                    notes_parts.append(f"  Affecting: {b.blocking_who}")

        description = "\n".join(notes_parts) if notes_parts else "Imported from GrowEasy."

        payload = {
            "meeting": {
                "title":       meeting.title or "Site Meeting",
                "date":        meeting_date,
                "description": description,
                "status":      "draft",
            }
        }

        r = requests.post(url, headers=self.headers, json=payload, timeout=15)

        if r.status_code not in (200, 201):
            raise Exception(
                f"Failed to create meeting: {r.status_code} — {r.text[:300]}"
            )

        data       = r.json()
        meeting_id = data.get("id") or data.get("meeting", {}).get("id")

        if self.is_mock:
            meeting_url = f"{self.procore_mode.mock_base_url}/store/"
        elif self.credential.is_sandbox:
            meeting_url = (
                f"https://sandbox.procore.com/{self.company_id}/project/"
                f"{self.procore_project_id}/meetings/{meeting_id}"
            )
        else:
            meeting_url = (
                f"https://app.procore.com/{self.company_id}/project/"
                f"{self.procore_project_id}/meetings/{meeting_id}"
            )

        return {
            "procore_meeting_id":  str(meeting_id),
            "procore_meeting_url": meeting_url,
        }

    def push_action_item(
        self,
        procore_meeting_id: str,
        task_description:   str,
        assignee_name:      str,
        due_date=None,
    ) -> dict:
        url = self._url(
            f"/projects/{self.procore_project_id}"
            f"/meetings/{procore_meeting_id}/meeting_action_items"
        )

        payload = {
            "meeting_action_item": {
                "title":       task_description[:255],
                "description": task_description,
                "due_date":    due_date.isoformat() if due_date else None,
                "assignee":    assignee_name or None,
                "status":      "initiated",
            }
        }

        r = requests.post(url, headers=self.headers, json=payload, timeout=15)

        if r.status_code not in (200, 201):
            raise Exception(
                f"Failed to create action item '{task_description[:40]}': "
                f"{r.status_code} — {r.text[:200]}"
            )

        data    = r.json()
        item_id = data.get("id") or data.get("meeting_action_item", {}).get("id")
        return {"procore_item_id": str(item_id)}


def push_meeting_to_procore(
    meeting,
    credential,
    assignee_overrides: dict = None,
    pusher_class=None,
) -> dict:
    from core.models import MeetingRecord, ProcoreMode
    from core.procore_oauth import get_valid_token

    if pusher_class is None:
        pusher_class = ProcoreMeetingPusher

    overrides     = assignee_overrides or {}
    procore_mode  = ProcoreMode.get()

    try:
        access_token = get_valid_token(credential)
    except Exception as exc:
        return {"success": False, "error": str(exc)}

    pusher = pusher_class(access_token, credential)

    ok, err = pusher.verify_credentials()
    if not ok and "expired" in err.lower() and credential.refresh_token:
        try:
            from core.procore_oauth import refresh_procore_token
            access_token = refresh_procore_token(credential)
            pusher = pusher_class(access_token, credential)
            ok, err = pusher.verify_credentials()
        except Exception:
            credential.is_connected = False
            credential.save(update_fields=["is_connected"])
            return {
                "success": False,
                "error": "Your Procore session expired. Reconnect from the setup page.",
            }

    if not ok:
        return {"success": False, "error": err}

    try:
        meeting_result = pusher.push_meeting(meeting)
    except Exception as exc:
        logger.exception("Failed to create Procore meeting for record %s", meeting.id)
        return {"success": False, "error": str(exc)}

    procore_meeting_id  = meeting_result["procore_meeting_id"]
    procore_meeting_url = meeting_result["procore_meeting_url"]

    items_pushed = 0
    items_failed = 0
    failures     = []

    for item in meeting.action_items.all().select_related("owner"):
        if str(item.id) in overrides:
            assignee_name = overrides[str(item.id)].strip()
        elif item.owner:
            assignee_name = item.owner.contact_name
        else:
            assignee_name = item.owner_raw_name or ""

        try:
            result = pusher.push_action_item(
                procore_meeting_id=procore_meeting_id,
                task_description=item.task_description,
                assignee_name=assignee_name,
                due_date=item.due_date,
            )
            item.procore_record_id = result["procore_item_id"]
            item.save(update_fields=["procore_record_id"])
            items_pushed += 1
        except Exception as exc:
            logger.error("Failed to push action item %s: %s", item.id, exc)
            failures.append({
                "task":  item.task_description[:60],
                "error": str(exc),
            })
            items_failed += 1

    meeting.status = MeetingRecord.Status.PUSHED
    meeting.save(update_fields=["status"])

    return {
        "success":             True,
        "procore_meeting_id":  procore_meeting_id,
        "procore_meeting_url": procore_meeting_url,
        "items_pushed":        items_pushed,
        "items_failed":        items_failed,
        "failures":            failures,
        "environment":         procore_mode.mode,
    }