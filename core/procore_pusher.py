"""
core/procore_pusher.py

Handles pushing a GrowEasy MeetingRecord to Procore via the Meetings API.

DESIGNED FOR EASY ENDPOINT SWAPPING:
Each company may use different Procore endpoints. The ProcoreMeetingPusher
class is the default (Meetings API). To support a company using Daily Logs,
subclass it and override the relevant push methods — the orchestrator
(push_meeting_to_procore) accepts any pusher instance.

Usage:
    from core.procore_pusher import push_meeting_to_procore, dry_run_summary

    # Dry run
    summary = dry_run_summary(meeting)

    # Real push
    result = push_meeting_to_procore(
        meeting=meeting,
        access_token="...",
        company_id="12345",
        procore_project_id="67890",
    )
"""

import logging
import requests
from datetime import date

logger = logging.getLogger(__name__)

#PROCORE_BASE = "https://api.procore.com/rest/v1.0"

PROCORE_BASE = "https://sandbox.procore.com/rest/v1.0"

#PROCORE_BASE = "https://sandbox.procore.com/4282700/company/home"


# ─────────────────────────────────────────────────────────
# DRY RUN — no API calls
# ─────────────────────────────────────────────────────────

def dry_run_summary(meeting) -> dict:
    """
    Returns a preview of what would be pushed to Procore.
    No API calls made. Called before real push.
    """
    action_items = list(meeting.action_items.all().select_related("owner"))
    decisions    = list(meeting.decisions.all())
    blockers     = list(meeting.blockers.all())

    assigned   = [i for i in action_items if i.owner or i.owner_raw_name]
    unassigned = [i for i in action_items if not i.owner and not i.owner_raw_name]

    return {
        "meeting_title":    meeting.title or "Untitled Meeting",
        "meeting_date":     meeting.meeting_date.isoformat() if meeting.meeting_date else None,
        "action_item_count": len(action_items),
        "assigned_count":   len(assigned),
        "unassigned_count": len(unassigned),
        "decision_count":   len(decisions),
        "blocker_count":    len(blockers),
        "action_items": [
            {
                "id":          i.id,
                "task":        i.task_description,
                "owner_name":  i.owner.contact_name if i.owner else (i.owner_raw_name or ""),
                "due_date":    i.due_date.isoformat() if i.due_date else None,
                "is_unassigned": not i.owner and not i.owner_raw_name,
            }
            for i in action_items
        ],
        "decisions": [
            {"description": d.description}
            for d in decisions
        ],
        "blockers": [
            {"description": b.description, "severity": b.severity}
            for b in blockers
        ],
    }


# ─────────────────────────────────────────────────────────
# BASE PUSHER — Procore Meetings API
# ─────────────────────────────────────────────────────────

class ProcoreMeetingPusher:
    """
    Pushes a MeetingRecord to Procore using the Meetings API.

    TO SWAP ENDPOINTS FOR A SPECIFIC COMPANY:
    Subclass this and override push_action_item() or push_meeting().
    Pass your subclass instance to push_meeting_to_procore().

    Example for a company using a custom endpoint:
        class AcmePusher(ProcoreMeetingPusher):
            def push_action_item(self, item_data):
                # POST to Acme's custom Procore setup
                ...
    """

    def __init__(self, access_token: str, company_id: str, procore_project_id: str):
        self.access_token       = access_token
        self.company_id         = company_id
        self.procore_project_id = procore_project_id
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type":  "application/json",
            "Procore-Company-Id": str(company_id),
        }

    def _url(self, path: str) -> str:
        return f"{PROCORE_BASE_URL}{path}"

    def verify_credentials(self) -> tuple[bool, str]:
        """
        Quick check that token + company + project are valid.
        Returns (True, "") or (False, error_message).
        """
        url = self._url(f"/projects/{self.procore_project_id}")
        try:
            r = requests.get(url, headers=self.headers, timeout=10)
            if r.status_code == 200:
                return True, ""
            elif r.status_code == 401:
                return False, "Invalid access token. Check your Procore credentials."
            elif r.status_code == 403:
                return False, "Access denied. You may not have permission for this project."
            elif r.status_code == 404:
                return False, f"Project ID {self.procore_project_id} not found in Procore."
            else:
                return False, f"Procore returned {r.status_code}: {r.text[:200]}"
        except requests.Timeout:
            return False, "Procore API timed out. Try again."
        except requests.ConnectionError:
            return False, "Could not reach Procore API. Check your connection."

    def push_meeting(self, meeting) -> dict:
        """
        Creates a Meeting record in Procore.
        Returns {"procore_meeting_id": str, "procore_meeting_url": str}
        """
        url = self._url(f"/projects/{self.procore_project_id}/meetings")

        meeting_date = (
            meeting.meeting_date.isoformat()
            if meeting.meeting_date
            else date.today().isoformat()
        )

        # Build description from decisions and blockers
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
                "status":      "draft",   # Creates as draft in Procore
            }
        }

        r = requests.post(url, headers=self.headers, json=payload, timeout=15)

        if r.status_code not in (200, 201):
            raise Exception(
                f"Failed to create Procore meeting: {r.status_code} — {r.text[:300]}"
            )

        data = r.json()
        meeting_id  = data.get("id") or data.get("meeting", {}).get("id")
        meeting_url = data.get("url") or (
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
        """
        Creates a single action item under a Procore meeting.
        Returns {"procore_item_id": str}
        """
        url = self._url(
            f"/projects/{self.procore_project_id}"
            f"/meetings/{procore_meeting_id}/meeting_action_items"
        )

        payload = {
            "meeting_action_item": {
                "title":       task_description[:255],
                "description": task_description,
                "due_date":    due_date.isoformat() if due_date else None,
                # Procore accepts assignee as a name string when no user ID available
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

        data = r.json()
        item_id = data.get("id") or data.get("meeting_action_item", {}).get("id")
        return {"procore_item_id": str(item_id)}


# ─────────────────────────────────────────────────────────
# ORCHESTRATOR — called by the view
# ─────────────────────────────────────────────────────────

def push_meeting_to_procore(
    meeting,
    access_token:       str,
    company_id:         str,
    procore_project_id: str,
    assignee_overrides: dict = None,  # {action_item_id: "name string"}
    pusher_class=None,                # swap for company-specific pusher
) -> dict:
    """
    Full push orchestrator.

    assignee_overrides: dict mapping ActionItem.id → name string
        PM-typed names for unassigned items from the pre-push UI.
        e.g. {42: "Emmanuel Osei", 43: ""}

    pusher_class: optional subclass of ProcoreMeetingPusher
        Pass a custom class to use a different Procore endpoint structure.

    Returns:
    {
        "success": bool,
        "procore_meeting_id": str,
        "procore_meeting_url": str,
        "items_pushed": int,
        "items_failed": int,
        "failures": [{"task": str, "error": str}],
    }
    """
    from core.models import MeetingRecord

    if pusher_class is None:
        pusher_class = ProcoreMeetingPusher

    pusher = pusher_class(access_token, company_id, procore_project_id)
    overrides = assignee_overrides or {}

    # ── 1. Verify credentials ──────────────────────────
    ok, err = pusher.verify_credentials()
    if not ok:
        return {"success": False, "error": err}

    # ── 2. Create meeting in Procore ───────────────────
    try:
        meeting_result = pusher.push_meeting(meeting)
    except Exception as exc:
        logger.exception("Failed to create Procore meeting for record %s", meeting.id)
        return {"success": False, "error": str(exc)}

    procore_meeting_id  = meeting_result["procore_meeting_id"]
    procore_meeting_url = meeting_result["procore_meeting_url"]

    # ── 3. Push action items ───────────────────────────
    items_pushed = 0
    items_failed = 0
    failures     = []

    action_items = list(meeting.action_items.all().select_related("owner"))

    for item in action_items:
        # Resolve assignee name: override → contact_name → owner_raw_name → ""
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
            # Store Procore record ID on the action item
            item.procore_record_id = result["procore_item_id"]
            item.save(update_fields=["procore_record_id"])
            items_pushed += 1

        except Exception as exc:
            logger.error(
                "Failed to push action item %s: %s", item.id, exc
            )
            failures.append({
                "task":  item.task_description[:60],
                "error": str(exc),
            })
            items_failed += 1

    # ── 4. Update MeetingRecord status ─────────────────
    meeting.status = MeetingRecord.Status.PUSHED
    meeting.save(update_fields=["status"])

    return {
        "success":            True,
        "procore_meeting_id":  procore_meeting_id,
        "procore_meeting_url": procore_meeting_url,
        "items_pushed":        items_pushed,
        "items_failed":        items_failed,
        "failures":            failures,
    }