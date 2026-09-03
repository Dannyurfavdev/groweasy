# core/procore_export.py

import requests


# ─────────────────────────────────────────────
# 1. DAILY LOG
# ─────────────────────────────────────────────

def build_daily_log(message):
    # FIX: message.sender_phone → message.sender
    sender   = message.sender_name or message.sender or "Unknown"
    trade    = message.trade_detected or "Unknown trade"
    location = message.location_detected or "Unknown location"

    header = f"[{sender} — {trade}, {location}]"
    body   = message.body or ""

    metadata_lines = []
    if message.trade_detected:
        metadata_lines.append(f"Trade: {message.trade_detected}")
    if message.location_detected:
        metadata_lines.append(f"Location: {message.location_detected}")
    if message.sentiment:
        metadata_lines.append(f"Sentiment: {message.sentiment}")
    if message.is_delay and message.delay_reason:
        metadata_lines.append(f"Delay reason: {message.delay_reason}")
    metadata_lines.append("Sent via GrowEasy")

    notes = f"{header}\n{body}\n\n{' | '.join(metadata_lines)}"

    weather_keywords = ["rain", "storm", "flood", "wind", "snow", "lightning"]
    is_weather_delay = (
        message.is_delay and
        any(kw in (message.body or "").lower() for kw in weather_keywords)
    )

    return {
        "daily_log": {
            "date":       message.created_at.strftime("%Y-%m-%d"),
            "notes":      notes,
            "weather":    "Rain" if is_weather_delay else "Clear",
            "created_by": "GrowEasy",
        }
    }


# ─────────────────────────────────────────────
# 2. OBSERVATION
# ─────────────────────────────────────────────

def build_observation(alert):
    ALERT_TYPE_MAP = {
        "WEATHER": "weather_delay",
        "DELAY":   "delay",
    }
    obs_type = ALERT_TYPE_MAP.get(
        (alert.alert_type or "").upper(),
        "delay"
    )

    location = ""
    trade    = ""
    if alert.related_message:
        location = alert.related_message.location_detected or ""
        trade    = alert.related_message.trade_detected or ""

    return {
        "observation_item": {
            "title":       alert.title or "Delay Alert",
            "description": alert.description or "",
            "status":      "open",
            "type":        obs_type,
            "location":    location,
            "trade":       trade,
            "created_by":  "GrowEasy",
        }
    }


# ─────────────────────────────────────────────
# 3. PHOTO
# ─────────────────────────────────────────────

def build_photo_payload(file_obj):
    # FIX: file_obj.trade → file_obj.trade_tag
    # FIX: file_obj.location → file_obj.location_tag
    parts = []
    if file_obj.trade_tag:
        parts.append(file_obj.trade_tag)
    if file_obj.location_tag:
        parts.append(file_obj.location_tag)

    description = " — ".join(parts) if parts else "Construction site photo"

    if file_obj.message:
        # FIX: sender_phone → sender
        sender = file_obj.message.sender_name or file_obj.message.sender
        if sender:
            description += f" | Sent by {sender}"
        if file_obj.message.created_at:
            description += f" | {file_obj.message.created_at.strftime('%Y-%m-%d')}"

    metadata = {
        "description": description,
        "source":      "GrowEasy",
    }

    try:
        response = requests.get(file_obj.file_path, timeout=15)
        response.raise_for_status()
        binary_content = response.content
    except requests.RequestException as e:
        print(f"[GrowEasy] Could not download photo {file_obj.id}: {e}")
        return None, None

    return metadata, binary_content


# ─────────────────────────────────────────────
# 4. PROJECT EXPORT (orchestrator)
# ─────────────────────────────────────────────

def build_project_export(project, date_from=None, date_to=None):
    from .models import Message, Alert, File

    # Messages → Daily Logs
    messages = Message.objects.filter(project=project).order_by("created_at")
    if date_from:
        messages = messages.filter(created_at__date__gte=date_from)
    if date_to:
        messages = messages.filter(created_at__date__lte=date_to)

    daily_logs = [build_daily_log(msg) for msg in messages]

    # Alerts → Observations
    alerts = Alert.objects.filter(
        project=project,
        alert_type__in=["DELAY", "WEATHER"]
    ).order_by("created_at")
    if date_from:
        alerts = alerts.filter(created_at__date__gte=date_from)
    if date_to:
        alerts = alerts.filter(created_at__date__lte=date_to)

    observations = [build_observation(alert) for alert in alerts]

    # Files → Photos
    # FIX: file_type="photo" → file_type="PHOTO"
    # NOTE: File uses uploaded_at, not created_at
    files = File.objects.filter(
        project=project,
        file_type="PHOTO"
    ).order_by("uploaded_at")
    if date_from:
        files = files.filter(uploaded_at__date__gte=date_from)
    if date_to:
        files = files.filter(uploaded_at__date__lte=date_to)

    photos = []
    skipped = 0
    for f in files:
        metadata, binary = build_photo_payload(f)
        if binary is not None:
            photos.append((metadata, binary, f.file_path))
        else:
            skipped += 1

    return {
        "project_id":   project.id,
        "project_name": project.name,
        "daily_logs":   daily_logs,
        "observations": observations,
        "photos":       photos,
        "summary": {
            "message_count":  len(daily_logs),
            "alert_count":    len(observations),
            "photo_count":    len(photos),
            "skipped_photos": skipped,
        }
    }