"""
core/meeting_extractor.py

Handles:
  1. File-to-text parsing  (txt, pdf, docx, plain paste)
  2. GPT-4 extraction with project-context injection
  3. Name → ProjectContact fuzzy matching
  4. Persisting results to DB
"""

import json
import logging
import re
from datetime import date, datetime
from difflib import SequenceMatcher

from django.utils import timezone

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 1. FILE PARSING
# ─────────────────────────────────────────────

def extract_text_from_file(uploaded_file) -> str:
    """
    Accepts a Django InMemoryUploadedFile or TemporaryUploadedFile.
    Returns plain text string.
    Supports: .txt, .pdf, .docx
    """
    name = uploaded_file.name.lower()

    if name.endswith(".txt"):
        return uploaded_file.read().decode("utf-8", errors="replace")

    if name.endswith(".pdf"):
        return _parse_pdf(uploaded_file)

    if name.endswith(".docx"):
        return _parse_docx(uploaded_file)

    # Fallback: try raw decode (e.g. .md, .text)
    try:
        return uploaded_file.read().decode("utf-8", errors="replace")
    except Exception:
        raise ValueError(f"Unsupported file type: {uploaded_file.name}")


def _parse_pdf(f) -> str:
    try:
        import fitz  # PyMuPDF
        data = f.read()
        doc = fitz.open(stream=data, filetype="pdf")
        return "\n".join(page.get_text() for page in doc)
    except ImportError:
        raise ImportError("PyMuPDF not installed. Run: pip install pymupdf")


def _parse_docx(f) -> str:
    try:
        from docx import Document
        import io
        doc = Document(io.BytesIO(f.read()))
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    except ImportError:
        raise ImportError("python-docx not installed. Run: pip install python-docx")


# ─────────────────────────────────────────────
# 2. GPT-4 EXTRACTION
# ─────────────────────────────────────────────

SYSTEM_PROMPT = """
You are an AI assistant specializing in construction project management.
You analyze meeting transcripts and extract structured information.
Always respond with valid JSON only — no markdown, no explanation, no preamble.
""".strip()


def build_extraction_prompt(transcript: str, project_name: str, contacts: list[dict]) -> str:
    """
    contacts: list of {"name": str, "phone": str, "role": str}
    """
    contacts_block = "\n".join(
        f"  - {c['contact_name']}" + (f" ({c['role']})" if c.get("role") else "")
        for c in contacts
    ) or "  (No contacts registered for this project yet)"

    today = date.today().isoformat()

    return f"""
You are analyzing a meeting transcript for construction project: "{project_name}"

Known project participants:
{contacts_block}

Today's date: {today}

Extract the following from the transcript and return ONLY a JSON object
with this exact schema. Use null for unknown values.

{{
  "meeting_date": "YYYY-MM-DD or null",
  "title": "short descriptive title for this meeting (max 60 chars)",
  "attendees": ["Name1", "Name2"],
  "decisions": [
    {{
      "description": "what was decided",
      "made_by": "person name or null"
    }}
  ],
  "action_items": [
    {{
      "task": "what needs to be done",
      "owner": "person responsible or null",
      "due_date": "YYYY-MM-DD or null"
    }}
  ],
  "blockers": [
    {{
      "description": "what is blocking progress",
      "blocking_who": "team or person affected or empty string",
      "severity": "LOW or MEDIUM or HIGH"
    }}
  ]
}}

Rules:
- Match owner/made_by names to the known participants list where possible.
  Use the exact name from the list, not the transcript spelling.
- If a due date is relative (e.g. "by Friday", "next week"), convert to an
  absolute date using today as reference: {today}
- Severity: HIGH = stops work, MEDIUM = slows work, LOW = minor issue
- Extract only real decisions, not discussions or maybes
- action_items must have a task; owner and due_date can be null

Transcript:
---
{transcript[:12000]}
---
""".strip()


def call_openai(prompt: str, system: str = SYSTEM_PROMPT) -> dict:
    """
    Calls OpenAI ChatCompletion. Returns parsed JSON dict.
    Raises on API error or JSON parse failure.
    """
    from django.conf import settings as s
    import logging
    logging.getLogger(__name__).info("KEY IN WORKER: %s", getattr(s, "OPENAI_API_KEY", "MISSING"))
    try:
        from openai import OpenAI
    except ImportError:
        raise ImportError("openai package not installed. Run: pip install openai")

    from django.conf import settings
    api_key = getattr(settings, "OPENAI_API_KEY", None)
    if not api_key:
        raise EnvironmentError("OPENAI_API_KEY is not set in environment.")

    client = OpenAI(api_key=api_key)

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": prompt},
        ],
        temperature=0.1,       # Low temp for structured extraction
        max_tokens=2000,
        response_format={"type": "json_object"},  # Forces JSON output
    )

    raw = response.choices[0].message.content
    return json.loads(raw)


# ─────────────────────────────────────────────
# 3. NAME → CONTACT FUZZY MATCHING
# ─────────────────────────────────────────────

def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def resolve_name_to_contact(raw_name: str, project_contacts) -> object | None:
    """
    Given a raw name string from the transcript,
    fuzzy-match against project contacts.
    Returns a ProjectContact instance or None.
    Threshold: 0.72 similarity score.
    """
    if not raw_name:
        return None

    best_match = None
    best_score = 0.0

    for contact in project_contacts:
        score = similarity(raw_name, contact.contact_name)
        if score > best_score:
            best_score = score
            best_match = contact

        # Also try first-name-only match (common in transcripts)
        first_name = contact.contact_name.split()[0] if contact.contact_name else ""
        if first_name:
            fn_score = similarity(raw_name, first_name)
            if fn_score > best_score:
                best_score = fn_score
                best_match = contact

    return best_match if best_score >= 0.72 else None


# ─────────────────────────────────────────────
# 4. MAIN ORCHESTRATOR
# ─────────────────────────────────────────────

def process_transcript(meeting_record_id: int):
    """
    Called by Celery task (or synchronously in dev).
    Loads MeetingRecord, runs extraction, saves results.
    """
    # Import here to avoid circular imports
    from core.models import MeetingRecord, ActionItem, Decision, Blocker, ProjectContact

    from django.conf import settings
    import logging
    logger = logging.getLogger(__name__)
    

    meeting = MeetingRecord.objects.select_related("project").get(id=meeting_record_id)
    meeting.status = MeetingRecord.Status.EXTRACTING
    meeting.save(update_fields=["status"])

    try:
        # Build context from project contacts
        contacts = list(
            ProjectContact.objects.filter(project=meeting.project)
            .values("contact_name", "phone_number", "role")
        )

        prompt = build_extraction_prompt(
            transcript=meeting.transcript_text,
            project_name=meeting.project.name,
            contacts=contacts,
        )

        extracted = call_openai(prompt)
        meeting.raw_ai_json = extracted

        # ── Resolve meeting metadata ──────────────────────
        if extracted.get("meeting_date"):
            try:
                meeting.meeting_date = datetime.strptime(
                    extracted["meeting_date"], "%Y-%m-%d"
                ).date()
            except ValueError:
                pass

        if extracted.get("title"):
            meeting.title = extracted["title"][:255]

        # ── Fetch contacts for fuzzy matching ────────────
        project_contacts = list(ProjectContact.objects.filter(project=meeting.project))

        # ── Persist Action Items ──────────────────────────
        for item in extracted.get("action_items", []):
            raw_owner = item.get("owner") or ""
            owner_contact = resolve_name_to_contact(raw_owner, project_contacts)
            logger.info("ACTION ITEM: task=%s owner=%s resolved=%s", item.get("task"), raw_owner, owner_contact)

            due = None
            if item.get("due_date"):
                try:
                    due = datetime.strptime(item["due_date"], "%Y-%m-%d").date()
                except ValueError:
                    pass

            ActionItem.objects.create(
                meeting=meeting,
                task_description=item.get("task", ""),
                owner=owner_contact,
                owner_raw_name=raw_owner,
                due_date=due,
            )

        # ── Persist Decisions ────────────────────────────
        for dec in extracted.get("decisions", []):
            raw_by = dec.get("made_by") or ""
            made_by_contact = resolve_name_to_contact(raw_by, project_contacts)

            Decision.objects.create(
                meeting=meeting,
                description=dec.get("description", ""),
                made_by=made_by_contact,
                made_by_raw_name=raw_by,
            )

        # ── Persist Blockers ─────────────────────────────
        for blk in extracted.get("blockers", []):
            severity = blk.get("severity", "MEDIUM").upper()
            if severity not in ("LOW", "MEDIUM", "HIGH"):
                severity = "MEDIUM"

            Blocker.objects.create(
                meeting=meeting,
                description=blk.get("description", ""),
                blocking_who=blk.get("blocking_who", ""),
                severity=severity,
            )

        meeting.status = MeetingRecord.Status.DRAFT
        meeting.error_message = ""

    except Exception as exc:
        logger.exception("Meeting extraction failed for record %s", meeting_record_id)
        meeting.status = MeetingRecord.Status.FAILED
        meeting.error_message = str(exc)

    finally:
        meeting.save()