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