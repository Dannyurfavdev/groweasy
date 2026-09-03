from celery import shared_task
from django.utils import timezone
import requests
from datetime import timedelta
import logging

logger = logging.getLogger(__name__)


# ================================================================
# NOTIFICATION HELPERS
# ================================================================

def broadcast_notification(project_id, notification_type, title, message, severity="warning"):
    """
    Push a real-time notification to all browsers watching this project.
    Called from Celery tasks — uses async_to_sync to bridge sync→async.
    Never raises — notification failure must never crash a task.
    """
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

        channel_layer = get_channel_layer()
        group_name = f"project_{project_id}_alerts"

        async_to_sync(channel_layer.group_send)(
            group_name,
            {
                "type":              "send.notification",
                "notification_type": notification_type,
                "title":             title,
                "message":           message,
                "severity":          severity,
                "project_id":        project_id,
                "timestamp":         timezone.now().isoformat(),
            }
        )
    except Exception as e:
        logger.warning(f"[Notifications] Failed to broadcast to project {project_id}: {e}")


def notify_on_message_analysis(message):
    """
    Called immediately after AI processing is saved on a message.
    Fires the right notification based on what was detected.
    """
    if not message.project_id:
        return

    project_id = message.project_id

    if message.is_delay:
        detail = ""
        if message.trade_detected:
            detail += f" in {message.trade_detected}"
        if message.location_detected:
            detail += f" at {message.location_detected}"
        if message.delay_reason:
            detail += f" — {message.delay_reason}"

        broadcast_notification(
            project_id=project_id,
            notification_type="delay",
            title="🚧 Delay Detected",
            message=f"Delay reported{detail}" if detail else "A delay has been reported on site",
            severity="danger"
        )

    elif message.sentiment == 'NEGATIVE':
        source = message.sender_name or message.sender or "A field contact"
        location_hint = f" at {message.location_detected}" if message.location_detected else ""
        trade_hint    = f" ({message.trade_detected})" if message.trade_detected else ""

        broadcast_notification(
            project_id=project_id,
            notification_type="negative",
            title="⚠️ Negative Update Received",
            message=f"{source} sent a negative update{location_hint}{trade_hint}",
            severity="warning"
        )


def notify_on_high_risk(project, snapshot):
    """
    Called from calculate_project_risk when a project tips into HIGH risk.
    """
    import re
    top_signal   = snapshot.signals[0] if snapshot.signals else "Multiple risk factors detected"
    clean_signal = re.sub(r'^[\U00010000-\U0010ffff\u2600-\u26FF\u2700-\u27BF\s]+', '', top_signal).strip()

    broadcast_notification(
        project_id=project.id,
        notification_type="risk_high",
        title=f"🔴 {project.name} — High Risk",
        message=f"Risk score: {snapshot.score}/100 · {clean_signal}",
        severity="danger"
    )


# ================================================================
# MESSAGE PROCESSING
# ================================================================

@shared_task
def process_message_async(message_id):
    """Process message with AI in background"""
    from .models import Message, Alert
    from .ai_processor import extract_construction_intel, detect_weather_delay

    try:
        msg = Message.objects.get(id=message_id)

        # Extract construction intelligence
        intel = extract_construction_intel(msg.body)

        # Update message with defaults for None values
        msg.trade_detected    = intel.get('trade') or ''
        msg.location_detected = intel.get('location') or ''
        msg.is_delay          = intel.get('is_delay', False)
        msg.delay_reason      = intel.get('delay_reason') or ''
        msg.processed         = True
        msg.save()

        # Fire real-time notification based on what was detected
        notify_on_message_analysis(msg)

        # Only create alerts if message has a project
        if msg.project:
            if detect_weather_delay(msg.body):
                Alert.objects.create(
                    project=msg.project,
                    alert_type='WEATHER',
                    title='Weather Delay Detected',
                    description=f"{msg.sender_name}: {msg.body[:200]}",
                    related_message=msg
                )
            elif msg.is_delay:
                Alert.objects.create(
                    project=msg.project,
                    alert_type='DELAY',
                    title=f'Delay: {msg.trade_detected or "Site"}',
                    description=msg.delay_reason or msg.body[:200],
                    related_message=msg
                )

        print(f"✅ Message {message_id} processed: Trade={msg.trade_detected}, Delay={msg.is_delay}")
        return intel

    except Message.DoesNotExist:
        print(f"❌ Message {message_id} not found")
        return None
    except Exception as e:
        print(f"❌ Error processing message {message_id}: {e}")
        try:
            msg = Message.objects.get(id=message_id)
            msg.trade_detected    = ''
            msg.location_detected = ''
            msg.processed         = True
            msg.save()
        except:
            pass
        return None


# ================================================================
# FILE PROCESSING
# ================================================================

@shared_task
def process_file_async(file_id):
    """Process uploaded files"""
    from .models import File
    from .ai_processor import transcribe_voice_note, describe_image, extract_construction_intel

    try:
        file = File.objects.get(id=file_id)

        if file.file_type == 'VOICE':
            audio_path    = download_file(file.file_path, f'/tmp/{file.id}.ogg')
            transcription = transcribe_voice_note(audio_path)
            file.transcription = transcription

            if transcription:
                intel = extract_construction_intel(transcription)
                file.trade_tag    = intel.get('trade') or ''
                file.location_tag = intel.get('location') or ''
                file.description  = intel.get('summary') or ''

        elif file.file_type == 'PHOTO':
            description  = describe_image(file.file_path)
            file.description = description

            intel = extract_construction_intel(description)
            file.trade_tag    = intel.get('trade') or ''
            file.location_tag = intel.get('location') or ''

        file.processed = True
        file.save()

        print(f"✅ File {file_id} processed: {file.file_type}")
        return True

    except Exception as e:
        print(f"❌ Error processing file {file_id}: {e}")
        return False


# ================================================================
# GOOGLE SHEETS / DRIVE SYNC
# ================================================================

@shared_task
def sync_google_sheets(project_id):
    """Sync Google Sheets data for a project"""
    from .models import Project, DataSource, SheetData
    from .integrations import read_google_sheet

    try:
        project = Project.objects.get(id=project_id)
        sources = DataSource.objects.filter(
            project=project,
            source_type='GSHEET',
            is_active=True
        )

        for source in sources:
            if not source.sheet_id:
                continue

            data = read_google_sheet(source.sheet_id, source.sheet_name)

            SheetData.objects.filter(source=source).delete()

            for idx, row in enumerate(data):
                SheetData.objects.create(
                    source=source,
                    row_data=row,
                    row_index=idx
                )

            source.last_synced = timezone.now()
            source.save()

            print(f"✅ Synced sheet {source.name}: {len(data)} rows")

        return True

    except Exception as e:
        print(f"❌ Error syncing sheets for project {project_id}: {e}")
        return False


@shared_task
def sync_google_drive(project_id):
    """Sync Google Drive files for a project"""
    from .models import Project, DataSource, File
    from .integrations import read_google_drive

    try:
        project = Project.objects.get(id=project_id)
        sources = DataSource.objects.filter(
            project=project,
            source_type='GDRIVE',
            is_active=True
        )

        for source in sources:
            if not source.folder_id:
                continue

            files = read_google_drive(source.folder_id)

            for drive_file in files:
                file_type = 'PDF' if 'pdf' in drive_file['mimeType'] else 'OTHER'

                File.objects.update_or_create(
                    project=project,
                    source='GDRIVE',
                    file_path=drive_file['id'],
                    defaults={
                        'name': drive_file['name'],
                        'file_type': file_type,
                    }
                )

            source.last_synced = timezone.now()
            source.save()

            print(f"✅ Synced Drive folder {source.name}: {len(files)} files")

        return True

    except Exception as e:
        print(f"❌ Error syncing Drive for project {project_id}: {e}")
        return False


@shared_task
def sync_all_projects():
    """Sync all active data sources for all projects"""
    from .models import Project, DataSource

    projects = Project.objects.all()

    for project in projects:
        if DataSource.objects.filter(project=project, source_type='GSHEET', is_active=True).exists():
            sync_google_sheets.delay(project.id)
        if DataSource.objects.filter(project=project, source_type='GDRIVE', is_active=True).exists():
            sync_google_drive.delay(project.id)

    print(f"✅ Triggered sync for {projects.count()} projects")
    return True


def download_file(url, save_path):
    """Helper to download file from URL"""
    response = requests.get(url, stream=True)
    with open(save_path, 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
    return save_path


# ================================================================
# RISK PULSE
# ================================================================

WEIGHT_NEGATIVE_SENTIMENT = 40
WEIGHT_DELAY_DETECTIONS   = 35
WEIGHT_SILENCE            = 25

WINDOW_HOURS              = 72
SILENCE_THRESHOLD_HOURS   = 24
MAX_DELAYS_FOR_FULL_SCORE = 5


def compute_risk_score(negative_pct, delay_count, is_silent):
    sentiment_score = negative_pct * WEIGHT_NEGATIVE_SENTIMENT
    delay_ratio     = min(delay_count / MAX_DELAYS_FOR_FULL_SCORE, 1.0)
    delay_score     = delay_ratio * WEIGHT_DELAY_DETECTIONS
    silence_score   = WEIGHT_SILENCE if is_silent else 0
    total           = sentiment_score + delay_score + silence_score
    return min(int(round(total)), 100)


def score_to_level(score, is_silent=False):
    if score >= 70:
        return 'high'
    elif score >= 40:
        return 'medium'
    elif is_silent:
        return 'medium'   # silence is never "healthy"
    else:
        return 'healthy'



def generate_signals(project, messages, negative_pct, delay_count, is_silent, window_hours):
    signals = []

    if is_silent:
        signals.append(f"⚠️ No field updates received in the last {SILENCE_THRESHOLD_HOURS} hours")

    if negative_pct >= 0.7:
        signals.append(f"🔴 {int(negative_pct * 100)}% of updates in the last {window_hours}hrs are negative")
    elif negative_pct >= 0.4:
        signals.append(f"🟡 Elevated negative sentiment — {int(negative_pct * 100)}% of recent updates")

    delay_messages = messages.filter(is_delay=True)
    if delay_count > 0:
        delay_trades = list(
            delay_messages
            .exclude(trade_detected__isnull=True)
            .exclude(trade_detected='')
            .values_list('trade_detected', flat=True)
            .distinct()
        )
        if delay_trades:
            trades_str = ', '.join(delay_trades[:3])
            if delay_count == 1:
                signals.append(f"🚧 Delay detected in {trades_str} work")
            else:
                signals.append(f"🚧 {delay_count} delays detected — {trades_str}")
        else:
            signals.append(f"🚧 {delay_count} delay{'s' if delay_count > 1 else ''} detected in last {window_hours}hrs")

        delay_reasons = list(
            delay_messages
            .exclude(delay_reason__isnull=True)
            .exclude(delay_reason='')
            .values_list('delay_reason', flat=True)
            .distinct()[:2]
        )
        for reason in delay_reasons:
            signals.append(f"   → Reason: {reason}")

    affected_locations = list(
        messages
        .filter(sentiment='NEGATIVE')
        .exclude(location_detected__isnull=True)
        .exclude(location_detected='')
        .values_list('location_detected', flat=True)
        .distinct()
    )
    if len(affected_locations) >= 2:
        locs_str = ', '.join(affected_locations[:3])
        signals.append(f"📍 Negative reports from: {locs_str}")

    if not signals:
        msg_count = messages.count()
        signals.append(f"✅ {msg_count} updates received — no issues detected")

    return signals


@shared_task(name='core.tasks.calculate_project_risk')
def calculate_project_risk(project_id=None):
    from core.models import Project, Message, RiskSnapshot

    if project_id:
        projects = Project.objects.filter(id=project_id)
    else:
        projects = Project.objects.all()

    window_start      = timezone.now() - timedelta(hours=WINDOW_HOURS)
    silence_threshold = timezone.now() - timedelta(hours=SILENCE_THRESHOLD_HOURS)

    for project in projects:
        try:
            messages    = Message.objects.filter(project=project, created_at__gte=window_start)
            total_count = messages.count()

            if total_count > 0:
                negative_count = messages.filter(sentiment='NEGATIVE').count()
                negative_pct   = negative_count / total_count
            else:
                negative_pct = 0.0

            delay_count = messages.filter(is_delay=True).count()
            is_silent   = not Message.objects.filter(
                project=project,
                created_at__gte=silence_threshold
            ).exists()

            score      = compute_risk_score(negative_pct, delay_count, is_silent)
            risk_level = score_to_level(score, is_silent)
            signals    = generate_signals(
                project, messages,
                negative_pct, delay_count, is_silent,
                WINDOW_HOURS
            )

            snapshot = RiskSnapshot.objects.create(
                project=project,
                score=score,
                risk_level=risk_level,
                negative_sentiment_pct=round(negative_pct, 3),
                delay_count=delay_count,
                is_silent=is_silent,
                message_count=total_count,
                signals=signals,
                window_hours=WINDOW_HOURS
            )

            # Fire notification if project just hit HIGH risk
            if snapshot.risk_level == 'high':
                notify_on_high_risk(project, snapshot)

            # Keep only 10 most recent snapshots per project
            old_snapshots = (
                RiskSnapshot.objects
                .filter(project=project)
                .order_by('-created_at')
                .values_list('id', flat=True)[10:]
            )
            if old_snapshots:
                RiskSnapshot.objects.filter(id__in=list(old_snapshots)).delete()

            logger.info(
                f"[RiskPulse] {project.name}: {risk_level.upper()} "
                f"(score={score}, neg={negative_pct:.0%}, "
                f"delays={delay_count}, silent={is_silent})"
            )

        except Exception as e:
            logger.error(f"[RiskPulse] Failed for project {project.id}: {e}", exc_info=True)

    return f"Risk snapshots computed for {projects.count()} projects"


############# PROCESS MEETING TASKS AND NOTIFY ACTION ITEM OWNERS #####################

@shared_task(bind=True, max_retries=2, default_retry_delay=30)
def process_meeting_transcript(self, meeting_record_id: int):
    """
    Async Celery task: runs GPT-4 extraction on a MeetingRecord.
    Called immediately after upload. PM sees status → EXTRACTING → DRAFT.

    Retries up to 2 times on transient failures (network, rate limit).
    """
    try:
        from core.meeting_extractor import process_transcript
        logger.info("Starting extraction for MeetingRecord id=%s", meeting_record_id)
        process_transcript(meeting_record_id)
        logger.info("Extraction complete for MeetingRecord id=%s", meeting_record_id)

    except Exception as exc:
        logger.warning(
            "Extraction failed for MeetingRecord id=%s: %s — retrying",
            meeting_record_id, exc
        )
        raise self.retry(exc=exc)


@shared_task
def notify_action_item_owners(meeting_record_id: int):
    """
    After PM approves a meeting, send WhatsApp messages to action item owners.
    Only sends to contacts who have a phone number and haven't been notified yet.
    """
    from core.models import MeetingRecord, ActionItem
    from django.utils import timezone

    try:
        meeting = MeetingRecord.objects.select_related("project").get(id=meeting_record_id)
    except MeetingRecord.DoesNotExist:
        logger.error("MeetingRecord %s not found for notifications", meeting_record_id)
        return

    items = ActionItem.objects.filter(
        meeting=meeting,
        owner__isnull=False,
        notified_at__isnull=True,          # Not yet notified
        status=ActionItem.Status.OPEN,
    ).select_related("owner")

    if not items.exists():
        logger.info("No notifiable action items for meeting %s", meeting_record_id)
        return

    sent_count = 0
    for item in items:
        phone = item.owner.phone_number
        if not phone:
            continue

        message = _build_whatsapp_message(item, meeting)
        success = _send_whatsapp(phone, message)

        if success:
            item.notified_at = timezone.now()
            item.save(update_fields=["notified_at"])
            sent_count += 1

    logger.info(
        "Sent %s WhatsApp notifications for meeting %s",
        sent_count, meeting_record_id
    )


def _build_whatsapp_message(action_item, meeting) -> str:
    due_str = (
        action_item.due_date.strftime("%b %d, %Y")
        if action_item.due_date
        else "No deadline set"
    )
    owner_first = action_item.owner.contact_name.split()[0] if action_item.owner else "Hi"
    project_name = meeting.project.name
    meeting_title = meeting.title or "recent meeting"

    return (
        f"Hi {owner_first} 👋\n\n"
        f"You have an action item from the *{meeting_title}* "
        f"on project *{project_name}*:\n\n"
        f"📋 *Task:* {action_item.task_description}\n"
        f"📅 *Due:* {due_str}\n\n"
        f"Reply *DONE* when complete, or *BLOCKED* if you need help."
    )


def _send_whatsapp(phone: str, message: str) -> bool:
    """
    Sends a WhatsApp message via Twilio.
    Reuses same Twilio config already in settings.
    Returns True on success.
    """
    import os
    try:
        from twilio.rest import Client
        from django.conf import settings
        account_sid = getattr(settings, "TWILIO_ACCOUNT_SID", None)
        auth_token  = getattr(settings, "TWILIO_AUTH_TOKEN", None)
        from_number = getattr(settings, "TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")

        if not account_sid or not auth_token:
            logger.warning("Twilio credentials not configured — skipping WhatsApp send")
            return False

        client = Client(account_sid, auth_token)

        # Normalize phone to whatsapp: format
        to_number = phone if phone.startswith("whatsapp:") else f"whatsapp:{phone}"

        client.messages.create(
            from_=from_number,
            to=to_number,
            body=message,
        )
        return True

    except Exception as exc:
        logger.error("WhatsApp send failed to %s: %s", phone, exc)
        return False

