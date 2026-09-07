from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponse, JsonResponse
from django.contrib.auth.decorators import login_required
from django.contrib.auth import authenticate, login, logout
from guardian.shortcuts import get_objects_for_user
from django.core.mail import BadHeaderError, send_mail
import json
from django.conf import settings
from django.views.decorators.http import require_POST, require_http_methods
from .procore_export import build_project_export
from .procore_client import send_project_export


from .ai_processor import (
    analyze_sentiment, 
    extract_construction_intel,
    detect_weather_delay,
)
from .tasks import process_message_async
from django.utils import timezone
from datetime import timedelta

import uuid
from datetime import datetime
from django.contrib.admin.views.decorators import staff_member_required
from .procore_mock_views import (
    _mock_store,
    _check_auth,
    _log_request,
    mock_procore_me,
    mock_procore_project,
    mock_procore_meeting,
    mock_procore_action_item,
    mock_procore_store,
)


import logging

logger = logging.getLogger(__name__)


@csrf_exempt
def whatsapp_webhook(request):
    """Handle incoming WhatsApp messages with phone number mapping"""
    from .models import Message, File, Project, ProjectContact
    
    if request.method == 'POST':
        msg_body = request.POST.get('Body', '')
        sender = request.POST.get('From', '')
        profile_name = request.POST.get('ProfileName', 'Unknown')
        num_media = int(request.POST.get('NumMedia', 0))
        
        print(f"📩 Message from {profile_name} ({sender}): {msg_body}")
        
        # Extract phone number (remove 'whatsapp:' prefix)
        sender_number = sender.replace('whatsapp:', '')
        
        # Look up project by phone number mapping
        project = None
        try:
            contact = ProjectContact.objects.filter(
                phone_number=sender_number,
                is_active=True
            ).select_related('project').first()
            
            if contact:
                project = contact.project
                print(f"📍 Mapped to project: {project.name}")
            else:
                # No mapping found - assign to default project
                project = Project.objects.first()
                print(f"⚠️ No mapping for {sender_number}, using default: {project.name if project else 'None'}")
        except Exception as e:
            print(f"Error looking up project: {e}")
            project = Project.objects.first()
        
        # Quick sentiment analysis
        sentiment, score = analyze_sentiment(msg_body)
        
        # Create message record
        msg = Message.objects.create(
            project=project,
            sender=sender,
            sender_name=profile_name,
            body=msg_body,
            sentiment=sentiment,
            score=score
        )
        
        # Handle media attachments
        import tempfile
        import requests
        from django.core.files.base import ContentFile
        from .ai_processor import transcribe_voice_note, describe_image

        for i in range(num_media):
            media_url = request.POST.get(f'MediaUrl{i}')
            media_type = request.POST.get(f'MediaContentType{i}', '')
            
            if media_url:
                twilio_account_sid = getattr(settings, 'TWILIO_ACCOUNT_SID', '')
                twilio_auth_token = getattr(settings, 'TWILIO_AUTH_TOKEN', '')
                
                if 'image' in media_type:
                    file_type = 'PHOTO'
                    
                    try:
                        # Download image
                        response = requests.get(
                            media_url,
                            auth=(twilio_account_sid, twilio_auth_token),
                            timeout=30
                        )
                        response.raise_for_status()
                        
                        filename = f"{profile_name}_{msg.created_at.strftime('%Y%m%d_%H%M%S')}_{i}.jpg"
                        
                        file_obj = File.objects.create(
                            project=project,
                            message=msg,
                            name=filename,
                            file_type=file_type,
                            source='WHATSAPP',
                            file_path=media_url
                        )
                        
                        # Save actual image file
                        file_obj.file.save(filename, ContentFile(response.content), save=True)
                        
                        print(f"📸 Saved image: {filename}")
                        
                    except Exception as e:
                        print(f"Image download error: {e}")
                        File.objects.create(
                            project=project,
                            message=msg,
                            name=f"{profile_name}_{msg.created_at.strftime('%Y%m%d_%H%M%S')}_{i}",
                            file_type=file_type,
                            source='WHATSAPP',
                            file_path=media_url
                        )
                
                elif 'audio' in media_type:
                    file_type = 'VOICE'
                    
                    try:
                        response = requests.get(
                            media_url, 
                            auth=(twilio_account_sid, twilio_auth_token), 
                            timeout=30
                        )
                        response.raise_for_status()
                        
                        with tempfile.NamedTemporaryFile(delete=False, suffix='.ogg') as tmp_file:
                            tmp_file.write(response.content)
                            tmp_path = tmp_file.name
                        
                        transcription = transcribe_voice_note(tmp_path)
                        
                        if transcription:
                            msg.body = f"[Voice Note] {transcription}"
                            sentiment, score = analyze_sentiment(transcription)
                            msg.sentiment = sentiment
                            msg.score = score
                            msg.save()
                            print(f"🎤 Transcribed: {transcription[:100]}")
                        
                        import os
                        os.unlink(tmp_path)
                        
                    except Exception as e:
                        print(f"Voice transcription error: {e}")
                        msg.body = "[Voice Note] (transcription failed)"
                        msg.save()
                    
                    # Create file record for voice note
                    File.objects.create(
                        project=project,
                        message=msg,
                        name=f"{profile_name}_{msg.created_at.strftime('%Y%m%d_%H%M%S')}_{i}",
                        file_type=file_type,
                        source='WHATSAPP',
                        file_path=media_url
                    )
                
                else:
                    file_type = 'OTHER'
                    File.objects.create(
                        project=project,
                        message=msg,
                        name=f"{profile_name}_{msg.created_at.strftime('%Y%m%d_%H%M%S')}_{i}",
                        file_type=file_type,
                        source='WHATSAPP',
                        file_path=media_url
                    )
        '''
        # Handle media attachments
        for i in range(num_media):
            media_url = request.POST.get(f'MediaUrl{i}')
            media_type = request.POST.get(f'MediaContentType{i}', '')
            
            if media_url:
                if 'image' in media_type:
                    file_type = 'PHOTO'
                elif 'audio' in media_type:
                    file_type = 'VOICE'
                else:
                    file_type = 'OTHER'
                
                File.objects.create(
                    project=project,
                    message=msg,
                    name=f"{profile_name}_{msg.created_at.strftime('%Y%m%d_%H%M%S')}_{i}",
                    file_type=file_type,
                    source='WHATSAPP',
                    file_path=media_url
                )
        '''
        
        # Queue async AI processing
        try:
            process_message_async.delay(msg.id)
        except Exception as e:
            # Fallback: process synchronously if Celery not available
            print(f"Celery not available, processing synchronously: {e}")
            from .models import Alert
            
            intel = extract_construction_intel(msg.body)
            msg.trade_detected = intel.get('trade') or ''
            msg.location_detected = intel.get('location') or ''
            msg.is_delay = intel.get('is_delay', False)
            msg.delay_reason = intel.get('delay_reason') or ''
            msg.processed = True
            msg.save()
            
            if msg.project and detect_weather_delay(msg.body):
                Alert.objects.create(
                    project=msg.project,
                    alert_type='WEATHER',
                    title='Weather Delay Detected',
                    description=f"{msg.sender_name}: {msg.body[:200]}",
                    related_message=msg
                )
        
        return HttpResponse("OK")
    
    return HttpResponse("Only POST allowed", status=405)


# Add this enhanced dashboard view to core/views.py

#@login_required
@login_required
def dashboard(request):
    """Main GrowEasy dashboard with filters and pagination"""
    from .models import Message, DataSource, File, Project, Alert, SheetData
    from django.db.models import Q
    from datetime import datetime, timedelta
    
    # Get all user's projects
    projects = get_objects_for_user(request.user, 'core.view_project', Project)
    selected_project_id = request.GET.get('project_id') or request.session.get('selected_project_id')
    
    current_project = None
    if selected_project_id:
        current_project = projects.filter(id=selected_project_id).first()
    
    if not current_project:
        current_project = projects.first()
    
    if not current_project:
        from django.contrib.auth.models import User
        user = request.user
        current_project = Project.objects.create(
            name='Demo Project',
            address='Demo Address',
            owner=user
        )
        projects = get_objects_for_user(request.user, 'core.view_project', Project)
    
    request.session['selected_project_id'] = current_project.id
    
    # ============ FILTERS & SEARCH ============
    
    # Get filter parameters from URL
    search_query = request.GET.get('search', '').strip()
    filter_sentiment = request.GET.get('sentiment', '')  # POSITIVE, NEGATIVE, NEUTRAL
    filter_trade = request.GET.get('trade', '')
    filter_delay = request.GET.get('delay', '')  # true/false
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    
    # Pagination
    page = int(request.GET.get('page', 1))
    per_page = int(request.GET.get('per_page', 20))  # Allow user to choose
    
    # Base queryset
    messages_queryset = Message.objects.filter(
        project=current_project
    ).prefetch_related('files')
    
    # Apply search filter
    if search_query:
        messages_queryset = messages_queryset.filter(
            Q(body__icontains=search_query) |
            Q(sender_name__icontains=search_query) |
            Q(trade_detected__icontains=search_query) |
            Q(location_detected__icontains=search_query)
        )
    
    # Apply sentiment filter
    if filter_sentiment:
        messages_queryset = messages_queryset.filter(sentiment=filter_sentiment)
    
    # Apply trade filter
    if filter_trade:
        messages_queryset = messages_queryset.filter(trade_detected=filter_trade)
    
    # Apply delay filter
    if filter_delay == 'true':
        messages_queryset = messages_queryset.filter(is_delay=True)
    
    # Apply date range filters
    if date_from:
        try:
            date_from_obj = datetime.strptime(date_from, '%Y-%m-%d')
            messages_queryset = messages_queryset.filter(created_at__gte=date_from_obj)
        except ValueError:
            pass
    
    if date_to:
        try:
            date_to_obj = datetime.strptime(date_to, '%Y-%m-%d')
            # Add one day to include the entire end date
            date_to_obj = date_to_obj + timedelta(days=1)
            messages_queryset = messages_queryset.filter(created_at__lt=date_to_obj)
        except ValueError:
            pass
    
    # Calculate stats on FILTERED queryset
    total_messages = messages_queryset.count()
    positive_count = messages_queryset.filter(sentiment='POSITIVE').count()
    negative_count = messages_queryset.filter(sentiment='NEGATIVE').count()
    sentiment_percent = (positive_count / total_messages * 100) if total_messages else 0
    
    # Pagination
    offset = (page - 1) * per_page
    messages = messages_queryset[offset:offset + per_page]
    
    total_pages = (total_messages + per_page - 1) // per_page  # Ceiling division
    has_next = page < total_pages
    has_prev = page > 1
    
    # Get unique trades for filter dropdown
    available_trades = Message.objects.filter(
        project=current_project,
        trade_detected__isnull=False
    ).exclude(trade_detected='').values_list('trade_detected', flat=True).distinct()
    
    # Rest of the dashboard logic (alerts, photos, sheets, drive)
    alerts = Alert.objects.filter(
        project=current_project,
        is_resolved=False
    ).order_by('-created_at')[:5]
    
    recent_photos = File.objects.filter(
        project=current_project,
        file_type='PHOTO'
    ).order_by('-uploaded_at')[:12]
    
    # Google Sheets (with auto-sync logic)
    from django.utils import timezone
    from datetime import timedelta
    
    # Replace single sheet logic with:
    sheet_sources = DataSource.objects.filter(
        project=current_project,
        source_type='GSHEET',
        is_active=True
    )

    all_sheet_data = []
    for sheet_source in sheet_sources:
        # Auto-sync if stale
        if not sheet_source.last_synced or (timezone.now() - sheet_source.last_synced) > timedelta(minutes=5):
            try:
                from .tasks import sync_google_sheets
                sync_google_sheets(current_project.id)
            except:
                pass

        # Get cached rows for this sheet
        rows = list(SheetData.objects.filter(source=sheet_source).order_by('row_index')[:10])

        # Safely extract column headers from first row
        # (works even if rows is empty)
        headers = list(rows[0].row_data.keys()) if rows else []

        # Always append — even empty sheets show as a tab with a sync button
        all_sheet_data.append({
            'name': sheet_source.name,
            'source_id': sheet_source.id,
            'rows': rows,
            'headers': headers,
            'last_synced': sheet_source.last_synced,
            'tab_id': f'sheet-{sheet_source.id}',  # unique ID for each tab
        })
    
    # Google Drive (with auto-sync logic)
    drive_source = DataSource.objects.filter(
        project=current_project,
        source_type='GDRIVE',
        is_active=True
    ).first()
    
    drive_files = []
    if drive_source:
        if not drive_source.last_synced or (timezone.now() - drive_source.last_synced) > timedelta(minutes=10):
            try:
                from .tasks import sync_google_drive
                try:
                    sync_google_drive.delay(current_project.id)
                except:
                    sync_google_drive(current_project.id)
            except Exception as e:
                print(f"Error auto-syncing Drive: {e}")
        
        drive_files = File.objects.filter(
            project=current_project,
            source='GDRIVE'
        ).order_by('-uploaded_at')[:10]
    
    suggested_docs = []
    
    context = {
        'project': current_project,
        'all_projects': projects,
        'messages': messages,
        'total_messages': total_messages,
        'positive_count': positive_count,
        'negative_count': negative_count,
        'sentiment_percent': sentiment_percent,
        'alerts': alerts,
        'recent_photos': recent_photos,
        'all_sheet_data': all_sheet_data,
        'drive_files': drive_files,
        'suggested_docs': suggested_docs,
        
        # Pagination
        'current_page': page,
        'total_pages': total_pages,
        'has_next': has_next,
        'has_prev': has_prev,
        'per_page': per_page,
        
        # Filters (for preserving state)
        'search_query': search_query,
        'filter_sentiment': filter_sentiment,
        'filter_trade': filter_trade,
        'filter_delay': filter_delay,
        'date_from': date_from,
        'date_to': date_to,
        'available_trades': list(available_trades),
    }
    
    return render(request, 'core/dashboard.html', context)



@login_required
def switch_project(request, project_id):
    """Switch active project"""
    from .models import Project
    
    projects = get_objects_for_user(request.user, 'core.view_project', Project)
    
    if projects.filter(id=project_id).exists():
        request.session['selected_project_id'] = project_id
        print(f"✅ Switched to project ID: {project_id}")
    
    return redirect('dashboard')


@login_required
def manage_contacts(request):
    """Manage phone number to project mappings"""
    from .models import Project, ProjectContact
    
    projects = get_objects_for_user(request.user, 'core.view_project', Project)
    selected_project_id = request.session.get('selected_project_id')
    current_project = projects.filter(id=selected_project_id).first() or projects.first()
    
    if request.method == 'POST':
        action = request.POST.get('action')
        
        '''
        if action == 'add':
            phone = request.POST.get('phone_number', '').strip()
            name = request.POST.get('contact_name', '').strip()
            role = request.POST.get('role', '').strip()
            project_id = request.POST.get('project_id')
            
            # Normalize phone number (add + if missing)
            if phone and not phone.startswith('+'):
                phone = '+' + phone
            
            try:
                project = projects.get(id=project_id)
                
                # Check if phone already exists
                existing = ProjectContact.objects.filter(phone_number=phone).first()
                if existing:
                    # Update to new project
                    existing.project = project
                    existing.contact_name = name
                    existing.role = role
                    existing.save()
                    message = f"✅ Updated {name} to {project.name}"
                else:
                    # Create new
                    ProjectContact.objects.create(
                        project=project,
                        phone_number=phone,
                        contact_name=name,
                        role=role
                    )
                    message = f"✅ Added {name} to {project.name}"
                
                return JsonResponse({'success': True, 'message': message})
            
            except Exception as e:
                return JsonResponse({'success': False, 'message': f'Error: {str(e)}'})
        '''

        if action == 'add':
            phone = request.POST.get('phone_number', '').strip()
            name = request.POST.get('contact_name', '').strip()
            role = request.POST.get('role', '').strip()
            project_id = request.POST.get('project_id')

            # Normalize phone number
            if phone and not phone.startswith('+'):
                phone = '+' + phone

            try:
                project = projects.get(id=project_id)

                # Check if phone already exists anywhere
                existing = ProjectContact.objects.filter(
                    phone_number=phone
                ).select_related('project').first()

                if existing:
                    # Same project — duplicate entry attempt
                    if existing.project_id == int(project_id):
                        return JsonResponse({
                            'success': False,
                            'message': (
                                f"⚠️ {existing.contact_name} ({phone}) is already a contact "
                                f"in {project.name}."
                            )
                        })
                    # Different project — warn PM and point to which project owns this number
                    else:
                        return JsonResponse({
                            'success': False,
                            'message': (
                                f"⚠️ This number ({phone}) is already assigned to "
                                f"{existing.contact_name} in '{existing.project.name}'. "
                                f"Each phone number can only belong to one project. "
                                f"Remove them from '{existing.project.name}' first if you "
                                f"want to reassign this number."
                            )
                        })

                # No conflict — create
                ProjectContact.objects.create(
                    project=project,
                    phone_number=phone,
                    contact_name=name,
                    role=role,
                )
                return JsonResponse({
                    'success': True,
                    'message': f"✅ {name} added to {project.name}."
                })

            except Exception as e:
                return JsonResponse({'success': False, 'message': f'Error: {str(e)}'})
        
        elif action == 'delete':
            contact_id = request.POST.get('contact_id')
            try:
                ProjectContact.objects.filter(id=contact_id).delete()
                return JsonResponse({'success': True, 'message': '✅ Contact removed'})
            except Exception as e:
                return JsonResponse({'success': False, 'message': f'Error: {str(e)}'})
    
    # Get all contacts for all projects
    all_contacts = ProjectContact.objects.filter(
        project__in=projects
    ).select_related('project').order_by('project__name', 'contact_name')
    
    context = {
        'all_projects': projects,
        'current_project': current_project,
        'all_contacts': all_contacts,
    }
    
    return render(request, 'core/manage_contacts.html', context)


"""
=================================================================
1. ADD THIS FUNCTION to core/views.py (standalone, not inside manage_contacts)
   It handles the CSV template download.
=================================================================
"""

@login_required
def download_contacts_template(request):
    """
    Returns a pre-built CSV template the PM can fill in and re-upload.
    """
    import csv
    from django.http import HttpResponse

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="groweasy_contacts_template.csv"'

    writer = csv.writer(response)

    # Header row
    writer.writerow(['phone_number', 'contact_name', 'role', 'project_name'])

    # Example rows so PM understands the format
    writer.writerow(['+2347068392922', 'Mike Johnson', 'Superintendent', 'Downtown Commercial Office Building'])
    writer.writerow(['+2348145414819', 'Sarah Chen',   'Site Supervisor', 'Harbor Mall'])
    writer.writerow(['+2348031234567', 'Emmanuel Osei','Roofer',          'Airport Project'])

    return response


"""
=================================================================
2. ADD THIS FUNCTION to core/views.py
   Handles the CSV upload and import logic.
=================================================================
"""

@login_required
@require_POST
def import_contacts_csv(request):
    """
    Accepts a CSV file upload with columns:
        phone_number, contact_name, role (optional), project_name

    - Matches project_name to projects the user owns (case-insensitive)
    - Skips rows where phone already exists (globally)
    - Returns a JSON summary: imported, skipped, errors
    """
    import csv
    import io
    from .models import Project, ProjectContact

    csv_file = request.FILES.get('csv_file')
    if not csv_file:
        return JsonResponse({'success': False, 'message': 'No file uploaded.'}, status=400)

    if not csv_file.name.endswith('.csv'):
        return JsonResponse({
            'success': False,
            'message': 'Only .csv files are accepted.'
        }, status=400)

    if csv_file.size > 2 * 1024 * 1024:  # 2MB cap
        return JsonResponse({
            'success': False,
            'message': 'File too large. Maximum size is 2MB.'
        }, status=400)

    # Load projects the user owns
    user_projects = {
        p.name.strip().lower(): p
        for p in Project.objects.filter(owner=request.user)
    }

    # Parse CSV
    try:
        decoded = csv_file.read().decode('utf-8-sig')  # utf-8-sig strips BOM from Excel CSVs
        reader  = csv.DictReader(io.StringIO(decoded))
    except Exception as exc:
        return JsonResponse({
            'success': False,
            'message': f'Could not read CSV: {exc}'
        }, status=400)

    # Validate headers
    required_headers = {'phone_number', 'contact_name', 'project_name'}
    if not reader.fieldnames:
        return JsonResponse({
            'success': False,
            'message': 'CSV appears to be empty.'
        }, status=400)

    actual_headers = {h.strip().lower() for h in reader.fieldnames}
    missing = required_headers - actual_headers
    if missing:
        return JsonResponse({
            'success': False,
            'message': f'Missing columns: {", ".join(missing)}. '
                       f'Download the template to see the correct format.'
        }, status=400)

    imported = 0
    skipped  = []   # [{row, reason}]
    errors   = []   # [{row, reason}]

    for row_num, row in enumerate(reader, start=2):  # start=2 because row 1 is header
        phone        = (row.get('phone_number') or '').strip()
        contact_name = (row.get('contact_name') or '').strip()
        role         = (row.get('role') or '').strip()
        project_name = (row.get('project_name') or '').strip()

        # ── Validate required fields ──────────────────────────
        if not phone:
            errors.append({'row': row_num, 'reason': 'Missing phone number'})
            continue
        if not contact_name:
            errors.append({'row': row_num, 'reason': f'Missing contact name for {phone}'})
            continue
        if not project_name:
            errors.append({'row': row_num, 'reason': f'Missing project name for {phone}'})
            continue

        # ── Normalize phone ───────────────────────────────────
        if not phone.startswith('+'):
            phone = '+' + phone

        # ── Match project ─────────────────────────────────────
        project = user_projects.get(project_name.lower())
        if not project:
            errors.append({
                'row':    row_num,
                'reason': f'Project "{project_name}" not found. '
                          f'Check the name matches exactly.'
            })
            continue

        # ── Check for existing phone globally ─────────────────
        existing = ProjectContact.objects.filter(
            phone_number=phone
        ).select_related('project').first()

        if existing:
            if existing.project_id == project.id:
                skipped.append({
                    'row':    row_num,
                    'reason': f'{contact_name} ({phone}) already exists in {project.name}'
                })
            else:
                skipped.append({
                    'row':    row_num,
                    'reason': (
                        f'{phone} is already assigned to {existing.contact_name} '
                        f'in "{existing.project.name}". '
                        f'Remove them there first to reassign.'
                    )
                })
            continue

        # ── Create contact ────────────────────────────────────
        try:
            ProjectContact.objects.create(
                project=project,
                phone_number=phone,
                contact_name=contact_name,
                role=role,
            )
            imported += 1
        except Exception as exc:
            errors.append({
                'row':    row_num,
                'reason': f'Could not save {contact_name}: {exc}'
            })

    # ── Build response summary ────────────────────────────────
    total_rows = imported + len(skipped) + len(errors)

    if imported == 0 and total_rows == 0:
        return JsonResponse({
            'success': False,
            'message': 'CSV file has no data rows.'
        })

    return JsonResponse({
        'success':  True,
        'imported': imported,
        'skipped':  skipped,
        'errors':   errors,
        'total':    total_rows,
    })


@login_required
def export_data(request):
    """Export dashboard data to CSV"""
    from .models import Message, File, Project, Alert
    import csv
    
    projects = get_objects_for_user(request.user, 'core.view_project', Project)
    selected_project_id = request.session.get('selected_project_id')
    current_project = projects.filter(id=selected_project_id).first() or projects.first()
    
    if not current_project:
        return HttpResponse("No project found", status=404)
    
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="groweasy_{current_project.name.replace(" ", "_")}.csv"'
    
    writer = csv.writer(response)
    
    writer.writerow(['=== MESSAGES ==='])
    writer.writerow(['Date', 'Sender', 'Message', 'Sentiment', 'Trade', 'Location', 'Delay?'])
    
    messages = Message.objects.filter(project=current_project).order_by('-created_at')
    for msg in messages:
        writer.writerow([
            msg.created_at.strftime('%Y-%m-%d %H:%M'),
            msg.sender_name,
            msg.body,
            msg.sentiment,
            msg.trade_detected or '',
            msg.location_detected or '',
            'YES' if msg.is_delay else 'NO'
        ])
    
    writer.writerow([])
    writer.writerow(['=== FILES ==='])
    writer.writerow(['Date', 'Name', 'Type', 'Trade', 'Location'])
    
    files = File.objects.filter(project=current_project).order_by('-uploaded_at')
    for file in files:
        writer.writerow([
            file.uploaded_at.strftime('%Y-%m-%d %H:%M'),
            file.name,
            file.file_type,
            file.trade_tag or '',
            file.location_tag or '',
        ])
    
    writer.writerow([])
    writer.writerow(['=== ALERTS ==='])
    writer.writerow(['Date', 'Type', 'Title', 'Description'])
    
    alerts = Alert.objects.filter(project=current_project).order_by('-created_at')
    for alert in alerts:
        writer.writerow([
            alert.created_at.strftime('%Y-%m-%d %H:%M'),
            alert.alert_type,
            alert.title,
            alert.description,
        ])
    
    return response


'''
# Replace export_data function in core/views.py

@login_required
def export_data(request):
    """Export dashboard data to CSV with filters"""
    from .models import Message, File, Project, Alert
    from django.db.models import Q
    from datetime import datetime, timedelta
    import csv
    
    projects = get_objects_for_user(request.user, 'core.view_project', Project)
    selected_project_id = request.session.get('selected_project_id')
    current_project = projects.filter(id=selected_project_id).first() or projects.first()
    
    if not current_project:
        return HttpResponse("No project found", status=404)
    
    # Get export parameters from query string
    export_type = request.GET.get('type', 'all')  # all, messages, files, alerts, recent
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    limit = request.GET.get('limit', '')  # e.g., 100, 500, 1000
    
    # Determine date range
    if not date_from and not date_to:
        # Default: last 30 days if no filter specified
        if export_type == 'recent':
            date_from_obj = datetime.now() - timedelta(days=30)
        else:
            date_from_obj = None
    else:
        date_from_obj = datetime.strptime(date_from, '%Y-%m-%d') if date_from else None
    
    date_to_obj = datetime.strptime(date_to, '%Y-%m-%d') if date_to else None
    if date_to_obj:
        date_to_obj = date_to_obj + timedelta(days=1)  # Include entire end date
    
    # Create CSV response
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'groweasy_{current_project.name.replace(" ", "_")}_{timestamp}.csv'
    
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    
    writer = csv.writer(response)
    
    # Export Messages
    if export_type in ['all', 'messages']:
        writer.writerow(['=== MESSAGES ==='])
        writer.writerow(['Date', 'Sender', 'Message', 'Sentiment', 'Trade', 'Location', 'Delay?', 'Delay Reason'])
        
        messages_qs = Message.objects.filter(project=current_project)
        
        # Apply date filter
        if date_from_obj:
            messages_qs = messages_qs.filter(created_at__gte=date_from_obj)
        if date_to_obj:
            messages_qs = messages_qs.filter(created_at__lt=date_to_obj)
        
        messages_qs = messages_qs.order_by('-created_at')
        
        # Apply limit
        if limit:
            try:
                limit_int = int(limit)
                messages_qs = messages_qs[:limit_int]
            except ValueError:
                pass
        
        message_count = 0
        for msg in messages_qs:
            writer.writerow([
                msg.created_at.strftime('%Y-%m-%d %H:%M'),
                msg.sender_name,
                msg.body,
                msg.sentiment,
                msg.trade_detected or '',
                msg.location_detected or '',
                'YES' if msg.is_delay else 'NO',
                msg.delay_reason or ''
            ])
            message_count += 1
        
        writer.writerow([])
        writer.writerow([f'Total Messages: {message_count}'])
        writer.writerow([])
    
    # Export Files
    if export_type in ['all', 'files']:
        writer.writerow(['=== FILES ==='])
        writer.writerow(['Date', 'Name', 'Type', 'Trade', 'Location', 'Description', 'Source'])
        
        files_qs = File.objects.filter(project=current_project)
        
        if date_from_obj:
            files_qs = files_qs.filter(uploaded_at__gte=date_from_obj)
        if date_to_obj:
            files_qs = files_qs.filter(uploaded_at__lt=date_to_obj)
        
        files_qs = files_qs.order_by('-uploaded_at')
        
        if limit:
            try:
                limit_int = int(limit)
                files_qs = files_qs[:limit_int]
            except ValueError:
                pass
        
        file_count = 0
        for file in files_qs:
            writer.writerow([
                file.uploaded_at.strftime('%Y-%m-%d %H:%M'),
                file.name,
                file.file_type,
                file.trade_tag or '',
                file.location_tag or '',
                file.description[:100] if file.description else '',
                file.source
            ])
            file_count += 1
        
        writer.writerow([])
        writer.writerow([f'Total Files: {file_count}'])
        writer.writerow([])
    
    # Export Alerts
    if export_type in ['all', 'alerts']:
        writer.writerow(['=== ALERTS ==='])
        writer.writerow(['Date', 'Type', 'Title', 'Description', 'Resolved?'])
        
        alerts_qs = Alert.objects.filter(project=current_project)
        
        if date_from_obj:
            alerts_qs = alerts_qs.filter(created_at__gte=date_from_obj)
        if date_to_obj:
            alerts_qs = alerts_qs.filter(created_at__lt=date_to_obj)
        
        alerts_qs = alerts_qs.order_by('-created_at')
        
        if limit:
            try:
                limit_int = int(limit)
                alerts_qs = alerts_qs[:limit_int]
            except ValueError:
                pass
        
        alert_count = 0
        for alert in alerts_qs:
            writer.writerow([
                alert.created_at.strftime('%Y-%m-%d %H:%M'),
                alert.alert_type,
                alert.title,
                alert.description,
                'YES' if alert.is_resolved else 'NO'
            ])
            alert_count += 1
        
        writer.writerow([])
        writer.writerow([f'Total Alerts: {alert_count}'])
    
    # Add export metadata
    writer.writerow([])
    writer.writerow(['=== EXPORT INFO ==='])
    writer.writerow(['Project', current_project.name])
    writer.writerow(['Exported By', request.user.username])
    writer.writerow(['Export Date', datetime.now().strftime('%Y-%m-%d %H:%M:%S')])
    if date_from:
        writer.writerow(['Date From', date_from])
    if date_to:
        writer.writerow(['Date To', date_to])
    if limit:
        writer.writerow(['Limit', limit])
    
    return response

'''

@login_required
def manage_projects(request):
    """Create and manage projects"""
    from .models import Project
    from guardian.shortcuts import assign_perm
    
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'create':
            name = request.POST.get('name', '').strip()
            address = request.POST.get('address', '').strip()
            
            if not name:
                return JsonResponse({'success': False, 'message': 'Project name is required'})
            
            try:
                # Create project
                project = Project.objects.create(
                    name=name,
                    address=address,
                    owner=request.user
                )
                
                # Assign permissions to creator
                assign_perm('view_project', request.user, project)
                
                # Set as active project
                request.session['selected_project_id'] = project.id
                
                return JsonResponse({
                    'success': True, 
                    'message': f'✅ Created project: {name}',
                    'project_id': project.id
                })
            
            except Exception as e:
                return JsonResponse({'success': False, 'message': f'Error: {str(e)}'})
        
        elif action == 'delete':
            project_id = request.POST.get('project_id')
            
            try:
                projects = get_objects_for_user(request.user, 'core.view_project', Project)
                project = projects.get(id=project_id)
                
                # Don't allow deleting if it's the only project
                if projects.count() <= 1:
                    return JsonResponse({
                        'success': False, 
                        'message': 'Cannot delete your only project'
                    })
                
                project_name = project.name
                project.delete()
                
                # Switch to another project
                new_project = projects.exclude(id=project_id).first()
                if new_project:
                    request.session['selected_project_id'] = new_project.id
                
                return JsonResponse({
                    'success': True, 
                    'message': f'✅ Deleted project: {project_name}'
                })
            
            except Exception as e:
                return JsonResponse({'success': False, 'message': f'Error: {str(e)}'})
        
        elif action == 'edit':
            project_id = request.POST.get('project_id')
            name = request.POST.get('name', '').strip()
            address = request.POST.get('address', '').strip()
            
            try:
                projects = get_objects_for_user(request.user, 'core.view_project', Project)
                project = projects.get(id=project_id)
                
                project.name = name
                project.address = address
                project.save()
                
                return JsonResponse({
                    'success': True, 
                    'message': f'✅ Updated project: {name}'
                })
            
            except Exception as e:
                return JsonResponse({'success': False, 'message': f'Error: {str(e)}'})
    
    # GET request - show projects page
    projects = get_objects_for_user(request.user, 'core.view_project', Project)
    
    context = {
        'all_projects': projects,
    }
    
    return render(request, 'core/manage_projects.html', context)

@login_required
def manage_data_sources(request):
    """Manage Google Sheets and Drive folders per project"""
    from .models import Project, DataSource
    
    projects = get_objects_for_user(request.user, 'core.view_project', Project)
    selected_project_id = request.session.get('selected_project_id')
    current_project = projects.filter(id=selected_project_id).first() or projects.first()
    
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'add':
            project_id = request.POST.get('project_id')
            source_type = request.POST.get('source_type')  # GSHEET or GDRIVE
            name = request.POST.get('name', '').strip()
            
            try:
                project = projects.get(id=project_id)
                
                if source_type == 'GSHEET':
                    sheet_id = request.POST.get('sheet_id', '').strip()
                    sheet_name = request.POST.get('sheet_name', 'Sheet1').strip()
                    
                    DataSource.objects.create(
                        project=project,
                        name=name,
                        source_type='GSHEET',
                        sheet_id=sheet_id,
                        sheet_name=sheet_name,
                        is_active=True
                    )
                    message = f"✅ Added Google Sheet: {name}"
                
                elif source_type == 'GDRIVE':
                    folder_id = request.POST.get('folder_id', '').strip()
                    
                    DataSource.objects.create(
                        project=project,
                        name=name,
                        source_type='GDRIVE',
                        folder_id=folder_id,
                        is_active=True
                    )
                    message = f"✅ Added Google Drive folder: {name}"
                
                else:
                    return JsonResponse({'success': False, 'message': 'Invalid source type'})
                
                return JsonResponse({'success': True, 'message': message})
            
            except Exception as e:
                return JsonResponse({'success': False, 'message': f'Error: {str(e)}'})
        
        elif action == 'delete':
            source_id = request.POST.get('source_id')
            try:
                DataSource.objects.filter(id=source_id).delete()
                return JsonResponse({'success': True, 'message': '✅ Data source removed'})
            except Exception as e:
                return JsonResponse({'success': False, 'message': f'Error: {str(e)}'})
        
        elif action == 'toggle':
            source_id = request.POST.get('source_id')
            try:
                source = DataSource.objects.get(id=source_id)
                source.is_active = not source.is_active
                source.save()
                status = "activated" if source.is_active else "deactivated"
                return JsonResponse({'success': True, 'message': f'✅ Data source {status}'})
            except Exception as e:
                return JsonResponse({'success': False, 'message': f'Error: {str(e)}'})
        
        elif action == 'sync':
            source_id = request.POST.get('source_id')
            try:
                source = DataSource.objects.get(id=source_id)
                
                if source.source_type == 'GSHEET':
                    from .tasks import sync_google_sheets
                    try:
                        sync_google_sheets.delay(source.project.id)
                        message = "✅ Sync started in background"
                    except:
                        sync_google_sheets(source.project.id)
                        message = "✅ Sync completed"
                
                elif source.source_type == 'GDRIVE':
                    from .tasks import sync_google_drive
                    try:
                        sync_google_drive.delay(source.project.id)
                        message = "✅ Sync started in background"
                    except:
                        sync_google_drive(source.project.id)
                        message = "✅ Sync completed"
                
                return JsonResponse({'success': True, 'message': message})
            except Exception as e:
                return JsonResponse({'success': False, 'message': f'Error: {str(e)}'})
    
    # Get all data sources grouped by project
    all_sources = DataSource.objects.filter(
        project__in=projects
    ).select_related('project').order_by('project__name', 'source_type', 'name')
    
    context = {
        'all_projects': projects,
        'current_project': current_project,
        'all_sources': all_sources,
    }
    
    return render(request, 'core/manage_data_sources.html', context)


@login_required
def trigger_sync(request):
    """Manually trigger Google Sheets AND Drive sync"""
    from .models import Project, DataSource
    from .tasks import sync_google_sheets, sync_google_drive
    
    projects = get_objects_for_user(request.user, 'core.view_project', Project)
    selected_project_id = request.session.get('selected_project_id')
    current_project = projects.filter(id=selected_project_id).first()
    
    if not current_project:
        return JsonResponse({'error': 'No project found'}, status=404)
    
    synced_sources = []
    
    # Check if project has Google Sheets
    has_sheets = DataSource.objects.filter(
        project=current_project, 
        source_type='GSHEET', 
        is_active=True
    ).exists()
    
    # Check if project has Google Drive
    has_drive = DataSource.objects.filter(
        project=current_project, 
        source_type='GDRIVE', 
        is_active=True
    ).exists()
    
    # Sync Sheets if available
    if has_sheets:
        try:
            sync_google_sheets.delay(current_project.id)
            synced_sources.append('Google Sheets (background)')
        except:
            sync_google_sheets(current_project.id)
            synced_sources.append('Google Sheets')
    
    # Sync Drive if available
    if has_drive:
        try:
            sync_google_drive.delay(current_project.id)
            synced_sources.append('Google Drive (background)')
        except:
            sync_google_drive(current_project.id)
            synced_sources.append('Google Drive')
    
    if synced_sources:
        return JsonResponse({
            'status': 'sync_started',
            'sources': synced_sources,
            'message': f"Syncing {', '.join(synced_sources)}"
        })
    else:
        return JsonResponse({
            'status': 'no_sources',
            'message': 'No active data sources to sync'
        })

'''
@login_required
def trigger_sync(request):
    """Manually trigger Google Sheets/Drive sync"""
    from .models import Project
    from .tasks import sync_google_sheets
    
    projects = get_objects_for_user(request.user, 'core.view_project', Project)
    selected_project_id = request.session.get('selected_project_id')
    current_project = projects.filter(id=selected_project_id).first()
    
    if current_project:
        try:
            sync_google_sheets.delay(current_project.id)
            return JsonResponse({'status': 'sync_started'})
        except:
            # Sync synchronously if Celery not available
            from .tasks import sync_google_sheets
            sync_google_sheets(current_project.id)
            return JsonResponse({'status': 'sync_completed'})
    
    return JsonResponse({'error': 'No project found'}, status=404)
'''


@login_required
def api_recent_messages(request):
    """Get recent messages as JSON for live updates"""
    from .models import Message, Project
    
    projects = get_objects_for_user(request.user, 'core.view_project', Project)
    selected_project_id = request.session.get('selected_project_id')
    current_project = projects.filter(id=selected_project_id).first()
    
    if not current_project:
        return JsonResponse({'messages': []})
    
    messages = Message.objects.filter(
        project=current_project
    ).order_by('-created_at')[:10]
    
    data = [{
        'id': msg.id,
        'sender': msg.sender_name,
        'body': msg.body,
        'sentiment': msg.sentiment,
        'trade': msg.trade_detected,
        'location': msg.location_detected,
        'is_delay': msg.is_delay,
        'created_at': msg.created_at.isoformat(),
    } for msg in messages]
    
    return JsonResponse({'messages': data})


@login_required
def api_alerts(request):
    """Get active alerts as JSON"""
    from .models import Alert, Project
    
    projects = get_objects_for_user(request.user, 'core.view_project', Project)
    selected_project_id = request.session.get('selected_project_id')
    current_project = projects.filter(id=selected_project_id).first()
    
    if not current_project:
        return JsonResponse({'alerts': []})
    
    alerts = Alert.objects.filter(
        project=current_project,
        is_resolved=False
    ).order_by('-created_at')[:5]
    
    data = [{
        'id': alert.id,
        'type': alert.alert_type,
        'title': alert.title,
        'description': alert.description,
        'created_at': alert.created_at.isoformat(),
    } for alert in alerts]
    
    return JsonResponse({'alerts': data})

@login_required
def view_full_sheet(request, source_id):
    """View all rows from a Google Sheet"""
    from .models import DataSource, SheetData, Project
    
    projects = get_objects_for_user(request.user, 'core.view_project', Project)
    
    try:
        # Get the data source
        source = DataSource.objects.get(
            id=source_id,
            project__in=projects,
            source_type='GSHEET'
        )
        
        # Get all cached rows
        all_rows = SheetData.objects.filter(source=source).order_by('row_index')
        
        context = {
            'source': source,
            'project': source.project,
            'all_rows': all_rows,
            'total_rows': all_rows.count(),
        }
        
        return render(request, 'core/view_full_sheet.html', context)
    
    except DataSource.DoesNotExist:
        return HttpResponse("Sheet not found", status=404)

@login_required
def risk_overview(request):
    """
    The Director's portfolio-level risk view.
    
    For each project the user has access to, we show:
    - Current risk level (from most recent RiskSnapshot)
    - Score
    - Top signal (first item from compressed signals list)
    - When it was last calculated
    
    On GET: we optionally trigger a fresh calculation for any
    project whose last snapshot is older than 6 hours.
    """
    from core.models import Project, RiskSnapshot
    from core.tasks import calculate_project_risk

    # Superuser sees all projects, regular users see only their own
    if request.user.is_superuser:
        user_projects = Project.objects.all()
    else:
        user_projects = Project.objects.filter(owner=request.user)

    '''
    # For each project, get the most recent snapshot
    # and optionally refresh if stale
    project_risks = []
    stale_threshold = timezone.now() - timedelta(hours=6)

    for project in user_projects:
        latest_snapshot = (
            RiskSnapshot.objects
            .filter(project=project)
            .order_by('-created_at')
            .first()
        )

        # Trigger fresh calculation if stale or no snapshot exists
        if not latest_snapshot or latest_snapshot.created_at < stale_threshold:
            # Run synchronously for immediate feedback on page load
            # (alternatively, .delay() for async — but then show a spinner)
            calculate_project_risk(project_id=project.id)
            latest_snapshot = (
                RiskSnapshot.objects
                .filter(project=project)
                .order_by('-created_at')
                .first()
            )

        # Build the context object for the template
        if latest_snapshot:
            top_signal = latest_snapshot.signals[0] if latest_snapshot.signals else "No data available"
            project_risks.append({
                'project': project,
                'snapshot': latest_snapshot,
                'top_signal': top_signal,
                'score': latest_snapshot.score,
                'risk_level': latest_snapshot.risk_level,
                'risk_color': latest_snapshot.risk_color,
                'risk_emoji': latest_snapshot.risk_emoji,
            })
        else:
            # Project has no messages yet
            project_risks.append({
                'project': project,
                'snapshot': None,
                'top_signal': 'No field data received yet',
                'score': 0,
                'risk_level': 'healthy',
                'risk_color': 'secondary',
                'risk_emoji': '⚪',
            })
    '''
    project_risks = []

    for project in user_projects:
        latest_snapshot = (
            RiskSnapshot.objects
            .filter(project=project)
            .order_by('-created_at')
            .first()
        )

        if latest_snapshot:
            top_signal = latest_snapshot.signals[0] if latest_snapshot.signals else "No issues detected"
            risk_emoji = {'healthy': '🟢', 'medium': '🟡', 'high': '🔴'}.get(latest_snapshot.risk_level, '⚪')
            risk_color = {'healthy': 'success', 'medium': 'warning', 'high': 'danger'}.get(latest_snapshot.risk_level, 'secondary')

            project_risks.append({
                'project': project,
                'snapshot': latest_snapshot,
                'top_signal': top_signal,
                'score': latest_snapshot.score,
                'risk_level': latest_snapshot.risk_level,
                'risk_color': risk_color,
                'risk_emoji': risk_emoji,
            })
        else:
            project_risks.append({
                'project': project,
                'snapshot': None,
                'top_signal': 'No field data received yet',
                'score': 0,
                'risk_level': 'healthy',
                'risk_color': 'secondary',
                'risk_emoji': '⚪',
            })

    risk_order = {'high': 0, 'medium': 1, 'healthy': 2}
    project_risks.sort(key=lambda x: risk_order.get(x['risk_level'], 3))

    context = {
    'project_risks': project_risks,
    'total_projects': len(project_risks),
    'high_count': sum(1 for p in project_risks if p['risk_level'] == 'high'),
    'medium_count': sum(1 for p in project_risks if p['risk_level'] == 'medium'),
    'healthy_count': sum(1 for p in project_risks if p['risk_level'] == 'healthy'),
    'last_updated': timezone.now(),
    }

    return render(request, 'core/risk_overview.html', context)


@login_required
def risk_detail(request, project_id):
    from core.models import Project, RiskSnapshot

    # Superuser can view any project, regular users only their own
    if request.user.is_superuser:
        project = get_object_or_404(Project, id=project_id)
    else:
        project = get_object_or_404(Project, id=project_id, owner=request.user)

    latest = (
        RiskSnapshot.objects
        .filter(project=project)
        .order_by('-created_at')
        .first()
    )

    history_start = timezone.now() - timedelta(days=7)
    history_snapshots = (
        RiskSnapshot.objects
        .filter(project=project, created_at__gte=history_start)
        .order_by('created_at')
        .values('score', 'risk_level', 'created_at')
    )

    history_labels = [
        s['created_at'].strftime('%b %d %H:%M')
        for s in history_snapshots
    ]
    history_scores = [s['score'] for s in history_snapshots]

    context = {
        'project': project,
        'snapshot': latest,
        'history_labels': history_labels,
        'history_scores': history_scores,
        'score_breakdown': {
            'sentiment_contribution': round(
                (latest.negative_sentiment_pct * 40) if latest else 0, 1
            ),
            'delay_contribution': round(
                (min(latest.delay_count / 5, 1.0) * 35) if latest else 0, 1
            ),
            'silence_contribution': 25 if (latest and latest.is_silent) else 0,
        } if latest else None,
    }

    return render(request, 'core/risk_detail.html', context)


@login_required
def risk_refresh_api(request, project_id):
    """
    API endpoint to manually trigger a risk recalculation.
    Called by the "Refresh" button in the UI via fetch().
    Returns JSON so the frontend can update without page reload.
    
    POST /risk/<project_id>/refresh/
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    from core.models import Project, RiskSnapshot
    from core.tasks import calculate_project_risk

    project = get_object_or_404(Project, id=project_id)

    # Recalculate
    calculate_project_risk(project_id=project.id)

    # Return the new snapshot data
    latest = (
        RiskSnapshot.objects
        .filter(project=project)
        .order_by('-created_at')
        .first()
    )

    return JsonResponse({
        'score': latest.score,
        'risk_level': latest.risk_level,
        'risk_emoji': latest.risk_emoji,
        'signals': latest.signals,
        'created_at': latest.created_at.isoformat(),
    })

@login_required
def risk_run_all(request):
    """Manually trigger risk calculation for all user's projects."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    from core.models import Project, RiskSnapshot
    from core.tasks import calculate_project_risk

    projects = Project.objects.filter(owner=request.user)

    for project in projects:
        calculate_project_risk(project_id=project.id)

    return JsonResponse({
        'success': True,
        'message': f'Risk recalculated for {projects.count()} projects'
    })



@login_required
@require_POST
def export_to_procore(request, project_id):
    """
    Exports WhatsApp messages (daily logs), alerts (observations),
    and photos to Procore using the saved ProcoreCredential.
 
    Active mode (mock/sandbox/production) is read from ProcoreMode.
    No manual token entry needed — credentials come from project setup.
 
    Supports dry_run=true for preview without sending.
    """
    from .models import Project, ProcoreCredential, ProcoreMode
    from .procore_export import build_project_export
    from .procore_client import send_project_export as _send
 
    # ── Verify project ownership ──────────────────────────
    try:
        project = Project.objects.get(id=project_id, owner=request.user)
    except Project.DoesNotExist:
        return JsonResponse({"error": "Project not found."}, status=404)
 
    # ── Parse request body ────────────────────────────────
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON in request body."}, status=400)
 
    date_from  = body.get("date_from") or None
    date_to    = body.get("date_to")   or None
    is_dry_run = body.get("dry_run", False)
 
    # ── Get active mode ───────────────────────────────────
    procore_mode = ProcoreMode.get()
 
    # ── Resolve credential ────────────────────────────────
    if procore_mode.is_mock:
        # Mock mode — use saved credential if available,
        # otherwise use a lightweight stand-in
        try:
            credential = project.procore_credential
        except ProcoreCredential.DoesNotExist:
            class _MockCred:
                company_id         = "mock-company"
                procore_project_id = "mock-project"
                environment        = "mock"
                is_sandbox         = False
                access_token       = "mock-token"
                refresh_token      = ""
                is_connected       = True
                @property
                def api_base_url(self):
                    return procore_mode.mock_base_url
            credential = _MockCred()
    else:
        # Sandbox / Production — require saved connected credential
        try:
            credential = project.procore_credential
        except ProcoreCredential.DoesNotExist:
            return JsonResponse({
                "error": (
                    "No Procore credentials set up for this project. "
                    "Go to Project Settings → Procore Setup, "
                    "or switch to Mock mode in Dev Tools."
                )
            }, status=400)
 
        if not credential.is_connected:
            return JsonResponse({
                "error": (
                    "Procore is not connected for this project. "
                    "Go to Project Settings → Procore Setup to reconnect, "
                    "or switch to Mock mode in Dev Tools."
                )
            }, status=400)
 
    # ── Build export payload ──────────────────────────────
    try:
        export_data = build_project_export(
            project,
            date_from=date_from,
            date_to=date_to,
        )
    except Exception as e:
        return JsonResponse({"error": f"Failed to build export: {e}"}, status=500)
 
    # ── Dry run — return counts only ──────────────────────
    if is_dry_run:
        return JsonResponse({
            "success":      True,
            "project_name": export_data["project_name"],
            "summary":      export_data["summary"],
            "mode":         procore_mode.mode,
        })
 
    # ── Real export ───────────────────────────────────────
    try:
        result = _send(export_data, credential=credential)
    except Exception as e:
        return JsonResponse({"error": f"Procore API error: {e}"}, status=502)
 
    return JsonResponse({
        "success":      True,
        "project_name": export_data["project_name"],
        "summary":      export_data["summary"],
        "result":       result,
        "mode":         procore_mode.mode,
    })

# ─────────────────────────────────────────────
# UPLOAD MEETING
# ─────────────────────────────────────────────

@login_required
def upload_meeting(request):
    """
    GET  → render upload form (project dropdown + file/paste input)
    POST → save MeetingRecord, fire Celery task, redirect to status page
    """
    from core.models import Project, MeetingRecord
    from core.tasks import process_meeting_transcript
    from core.meeting_extractor import extract_text_from_file

    projects = Project.objects.filter(
        owner=request.user    # only projects user belongs to
    ).distinct()

    if request.method == "GET":
        return render(request, "core/meeting_upload.html", {"projects": projects})

    # ── POST ─────────────────────────────────────
    project_id     = request.POST.get("project_id")
    transcript_raw = request.POST.get("transcript_text", "").strip()
    uploaded_file  = request.FILES.get("transcript_file")

    # Validation
    errors = {}
    if not project_id:
        errors["project"] = "Please select a project."
    if not transcript_raw and not uploaded_file:
        errors["transcript"] = "Paste a transcript or upload a file."

    if errors:
        return render(request, "core/meeting_upload.html", {
            "projects": projects,
            "errors": errors,
            "previous": request.POST,
        })

    project = get_object_or_404(Project, id=project_id)

    # Parse file if uploaded
    if uploaded_file:
        try:
            transcript_text = extract_text_from_file(uploaded_file)
        except Exception as exc:
            return render(request, "core/meeting_upload.html", {
                "projects": projects,
                "errors": {"transcript": f"Could not read file: {exc}"},
                "previous": request.POST,
            })
    else:
        transcript_text = transcript_raw

    if len(transcript_text.strip()) < 50:
        return render(request, "core/meeting_upload.html", {
            "projects": projects,
            "errors": {"transcript": "Transcript is too short to extract anything useful."},
            "previous": request.POST,
        })

    # Create record + fire task
    meeting = MeetingRecord.objects.create(
        project=project,
        transcript_text=transcript_text,
        uploaded_by=request.user,
        status=MeetingRecord.Status.PENDING,
    )

    process_meeting_transcript.delay(meeting.id)

    return redirect("meeting_status", pk=meeting.id)


# ─────────────────────────────────────────────
# STATUS (polling page + JSON endpoint)
# ─────────────────────────────────────────────

@login_required
def meeting_status(request, pk):
    """
    Renders a waiting/status page. JS polls the JSON endpoint every 3s.
    Once DRAFT, JS redirects to review page.
    """
    from core.models import MeetingRecord
    meeting = get_object_or_404(MeetingRecord, pk=pk, project__owner=request.user)
    return render(request, "core/meeting_status.html", {"meeting": meeting})


@login_required
def meeting_status_json(request, pk):
    """
    Polled by JS every 3s. Returns current status + redirect URL when ready.
    """
    from core.models import MeetingRecord
    meeting = get_object_or_404(MeetingRecord, pk=pk)

    data = {
        "status": meeting.status,
        "title":  meeting.title or "Untitled Meeting",
        "error":  meeting.error_message,
    }

    if meeting.status == MeetingRecord.Status.DRAFT:
        data["redirect"] = f"/meetings/{pk}/review/"

    return JsonResponse(data)


# ─────────────────────────────────────────────
# REVIEW + APPROVE
# ─────────────────────────────────────────────

@login_required
def meeting_review(request, pk):
    """
    Shows extracted action items, decisions, blockers for PM review.
    Inline editing is handled via individual PATCH endpoints below.
    """
    from core.models import MeetingRecord
    meeting = get_object_or_404(
        MeetingRecord.objects.prefetch_related(
            "action_items__owner",
            "decisions__made_by",
            "blockers",
        ),
        pk=pk,
        project__owner=request.user,
    )
    return render(request, "core/meeting_review.html", {"meeting": meeting})


@login_required
@require_POST
def meeting_approve(request, pk):
    from core.models import MeetingRecord
    from core.tasks import notify_action_item_owners

    meeting = get_object_or_404(MeetingRecord, pk=pk)

    if meeting.status != MeetingRecord.Status.DRAFT:
        return JsonResponse({"error": "Only DRAFT meetings can be approved."}, status=400)

    all_unassigned = meeting.action_items.filter(owner__isnull=True)

    # Named but unresolved — these BLOCK approval (person exists but not in contacts)
    named_unresolved = [
        i.owner_raw_name.strip()
        for i in all_unassigned
        if i.owner_raw_name and i.owner_raw_name.strip()
    ]

    # Truly ownerless — warn but allow approval
    ownerless_count = all_unassigned.filter(owner_raw_name="").count()

    if named_unresolved:
        return JsonResponse({
            "error": "unassigned_owners",
            "message": "Some action items have named owners who are not project contacts.",
            "unassigned": named_unresolved,
            "ownerless_count": ownerless_count,
        }, status=400)

    # Approve — ownerless tasks are allowed through with a warning
    meeting.status = MeetingRecord.Status.APPROVED
    meeting.save(update_fields=["status"])
    notify_action_item_owners.delay(meeting.id)

    return JsonResponse({
        "status": "approved",
        "notified": meeting.action_items.filter(owner__isnull=False).count(),
        "ownerless_count": ownerless_count,
    })


@login_required
@require_POST
def action_item_update(request, pk):
    """
    Inline edit for a single action item from the review screen.
    Accepts JSON body: { task_description, owner_raw_name, due_date, status }
    """
    from core.models import ActionItem, ProjectContact

    item = get_object_or_404(ActionItem, pk=pk)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    if "task_description" in data:
        item.task_description = data["task_description"]

    if "due_date" in data:
        from datetime import datetime
        try:
            item.due_date = datetime.strptime(data["due_date"], "%Y-%m-%d").date() if data["due_date"] else None
        except ValueError:
            return JsonResponse({"error": "Invalid date format. Use YYYY-MM-DD."}, status=400)

    if "status" in data:
        item.status = data["status"]

    if "owner_raw_name" in data:
        item.owner_raw_name = data["owner_raw_name"]
        # Try to re-resolve contact
        from core.meeting_extractor import resolve_name_to_contact
        contacts = ProjectContact.objects.filter(project=item.meeting.project)
        item.owner = resolve_name_to_contact(data["owner_raw_name"], contacts)

    item.save()
    return JsonResponse({"saved": True, "owner_resolved": item.owner.contact_name if item.owner else None})


@login_required
@require_POST
def action_item_delete(request, pk):
    from core.models import ActionItem
    item = get_object_or_404(ActionItem, pk=pk)
    item.delete()
    return JsonResponse({"deleted": True})


# ─────────────────────────────────────────────
# MEETINGS LIST (per project)
# ─────────────────────────────────────────────

@login_required
def meetings_list(request):
    """
    Lists all meetings across user's projects. Optional ?project=id filter.
    """
    from core.models import MeetingRecord, Project

    projects = Project.objects.filter(
        owner=request.user
    ).distinct()

    project_id = request.GET.get("project")
    meetings = MeetingRecord.objects.filter(
        project__owner=request.user
    ).select_related("project", "uploaded_by")

    if project_id:
        meetings = meetings.filter(project_id=project_id)

    return render(request, "core/meetings_list.html", {
        "meetings":   meetings,
        "projects":   projects,
        "project_id": project_id,
    })


# ─────────────────────────────────────────────────────────
# SETUP PAGE
# ─────────────────────────────────────────────────────────

@login_required
def procore_setup(request, pk):
    """
    GET  → renders credential setup form
    POST → saves/updates ProcoreCredential
    """
    from core.models import Project, ProcoreCredential

    project = get_object_or_404(Project, pk=pk, owner=request.user)

    try:
        credential = project.procore_credential
    except ProcoreCredential.DoesNotExist:
        credential = None

    if request.method == "GET":
        # Surface any OAuth result messages
        connected   = request.GET.get('connected')
        error       = request.GET.get('error')
        return render(request, "core/procore_setup.html", {
            "project":    project,
            "credential": credential,
            "connected":  connected,
            "oauth_error": error,
        })

    # POST — save credentials
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    client_id          = body.get("client_id", "").strip()
    client_secret      = body.get("client_secret", "").strip()
    company_id         = body.get("company_id", "").strip()
    procore_project_id = body.get("procore_project_id", "").strip()
    environment        = body.get("environment", "sandbox").strip()

    missing = []
    if not client_id:          missing.append("Client ID")
    if not client_secret:      missing.append("Client Secret")
    if not company_id:         missing.append("Company ID")
    if not procore_project_id: missing.append("Procore Project ID")

    if missing:
        return JsonResponse({
            "error": f"Missing fields: {', '.join(missing)}"
        }, status=400)

    if environment not in ("sandbox", "production"):
        environment = "sandbox"

    if credential:
        credential.client_id          = client_id
        credential.client_secret      = client_secret
        credential.company_id         = company_id
        credential.procore_project_id = procore_project_id
        credential.environment        = environment
        # Reset connection status when credentials change
        credential.is_connected  = False
        credential.access_token  = ''
        credential.refresh_token = ''
        credential.save()
    else:
        ProcoreCredential.objects.create(
            project=project,
            client_id=client_id,
            client_secret=client_secret,
            company_id=company_id,
            procore_project_id=procore_project_id,
            environment=environment,
        )

    return JsonResponse({
        "success":     True,
        "environment": environment,
        "message":     f"Credentials saved. Now click Connect to Procore to authorize.",
    })


# ─────────────────────────────────────────────────────────
# OAUTH REDIRECT + CALLBACK
# ─────────────────────────────────────────────────────────

@login_required
def procore_oauth_redirect(request, pk):
    from core.procore_oauth import procore_oauth_redirect as _redirect
    return _redirect(request, pk)


@login_required
def procore_oauth_callback(request):
    from core.procore_oauth import procore_oauth_callback as _callback
    return _callback(request)


# ─────────────────────────────────────────────────────────
# TEST CONNECTION
# ─────────────────────────────────────────────────────────

@login_required
@require_POST
def procore_test_connection(request, pk):
    """
    Tests the stored OAuth tokens by verifying project access.
    """
    from core.models import Project, ProcoreCredential
    from core.procore_oauth import get_valid_token
    from core.procore_pusher import ProcoreMeetingPusher

    project = get_object_or_404(Project, pk=pk, owner=request.user)

    try:
        credential = project.procore_credential
    except ProcoreCredential.DoesNotExist:
        return JsonResponse({
            "error": "No Procore credentials saved yet."
        }, status=400)

    if not credential.access_token and not credential.refresh_token:
        return JsonResponse({
            "error": "not_connected",
            "message": (
                "Credentials saved but not yet authorized. "
                "Click 'Connect to Procore' to complete setup."
            )
        }, status=400)

    try:
        token = get_valid_token(credential)
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    pusher = ProcoreMeetingPusher(token, credential)
    ok, err = pusher.verify_credentials()

    if not ok:
        return JsonResponse({"error": err}, status=400)

    # Mark as connected
    credential.is_connected = True
    credential.save(update_fields=["is_connected"])

    return JsonResponse({
        "success":     True,
        "environment": credential.environment,
        "message":     (
            f"Connected to Procore {credential.environment} successfully. "
            f"Project ID {credential.procore_project_id} is accessible."
        ),
    })


# ─────────────────────────────────────────────────────────
# DRY RUN
# ─────────────────────────────────────────────────────────

@login_required
@require_POST
def meeting_procore_dry_run(request, pk):
    from core.models import MeetingRecord, ProcoreCredential, ProcoreMode
    from core.procore_pusher import dry_run_summary
 
    meeting = get_object_or_404(
        MeetingRecord, pk=pk, project__owner=request.user,
    )
 
    procore_mode = ProcoreMode.get()
 
    # In mock mode — skip credential checks entirely
    if not procore_mode.is_mock:
        try:
            credential = meeting.project.procore_credential
        except ProcoreCredential.DoesNotExist:
            return JsonResponse({
                "error": "no_credentials",
                "message": (
                    "No Procore credentials set up for this project. "
                    "Go to Project Settings → Procore to connect, "
                    "or switch to Mock mode in Dev Tools."
                ),
            }, status=400)
 
        if not credential.is_connected:
            return JsonResponse({
                "error": "not_connected",
                "message": (
                    "Procore is not connected for this project. "
                    "Go to Project Settings → Procore to connect, "
                    "or switch to Mock mode in Dev Tools."
                ),
            }, status=400)
 
    summary = dry_run_summary(meeting)
    summary["environment"] = procore_mode.mode
    return JsonResponse({"success": True, "summary": summary})


# ─────────────────────────────────────────────────────────
# REAL PUSH
# ─────────────────────────────────────────────────────────

@login_required
@require_POST
def meeting_procore_push(request, pk):
    from core.models import MeetingRecord, ProcoreCredential, ProcoreMode
    from core.procore_pusher import push_meeting_to_procore
 
    meeting = get_object_or_404(
        MeetingRecord, pk=pk, project__owner=request.user,
    )
 
    if meeting.status not in (
        MeetingRecord.Status.APPROVED,
        MeetingRecord.Status.PUSHED,
    ):
        return JsonResponse({
            "error": "Meeting must be approved before pushing to Procore."
        }, status=400)
 
    procore_mode = ProcoreMode.get()
 
    # In mock mode — use a dummy credential object so the pusher
    # has something to work with (it reads company_id etc.)
    if procore_mode.is_mock:
        try:
            credential = meeting.project.procore_credential
        except ProcoreCredential.DoesNotExist:
            # Create a temporary in-memory credential for mock pushes
            # so the pusher has the project IDs it needs for URL building
            class MockCredential:
                company_id         = "mock-company"
                procore_project_id = "mock-project"
                environment        = "mock"
                is_sandbox         = False
                access_token       = "mock-token"
                refresh_token      = ""
                is_connected       = True
 
                @property
                def api_base_url(self):
                    return procore_mode.mock_base_url
 
            credential = MockCredential()
 
    else:
        try:
            credential = meeting.project.procore_credential
        except ProcoreCredential.DoesNotExist:
            return JsonResponse({
                "error": "no_credentials",
                "message": (
                    "No Procore credentials set up for this project. "
                    "Go to Project Settings → Procore to connect, "
                    "or switch to Mock mode in Dev Tools."
                ),
            }, status=400)
 
        if not credential.is_connected:
            return JsonResponse({
                "error": "not_connected",
                "message": (
                    "Procore is not connected. "
                    "Go to Project Settings → Procore to reconnect, "
                    "or switch to Mock mode in Dev Tools."
                ),
            }, status=400)
 
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
 
    assignee_overrides = body.get("assignee_overrides", {})
 
    result = push_meeting_to_procore(
        meeting=meeting,
        credential=credential,
        assignee_overrides=assignee_overrides,
    )
 
    if not result["success"]:
        error_msg = result.get("error", "Push failed.")
        # If token expired in sandbox/production, reset connection flag
        if not procore_mode.is_mock and hasattr(credential, 'is_connected'):
            if "expired" in error_msg.lower() or "reconnect" in error_msg.lower():
                credential.is_connected = False
                credential.save(update_fields=["is_connected"])
 
        return JsonResponse({"error": error_msg}, status=400)
 
    return JsonResponse(result)


@staff_member_required
def dev_tools(request):
    from core.models import Project, ProcoreCredential, ProcoreMode
    from core.procore_mock_views import _mock_store

    procore_mode = ProcoreMode.get()

    projects = []
    for p in Project.objects.filter(owner=request.user):
        try:
            cred = p.procore_credential
            projects.append({
                "project":      p,
                "credential":   cred,
                "environment":  cred.environment,
                "is_connected": cred.is_connected,
            })
        except ProcoreCredential.DoesNotExist:
            projects.append({
                "project":    p,
                "credential": None,
            })

    mock_meetings = list(_mock_store["meetings"].values())

    return render(request, "core/dev_tools.html", {
        "procore_mode":             procore_mode,
        "projects":                 projects,
        "mock_meeting_count":       len(_mock_store.get("meetings", {})),
        "mock_action_item_count":   len(_mock_store.get("action_items", {})),
        "mock_daily_log_count":     len(_mock_store.get("daily_logs", {})),
        "mock_observation_count":   len(_mock_store.get("observations", {})),
        "mock_image_count":         len(_mock_store.get("images", {})),
        "mock_meetings":            list(_mock_store.get("meetings", {}).values()),
        "mock_daily_logs":          list(_mock_store.get("daily_logs", {}).values()),
        "mock_observations":        list(_mock_store.get("observations", {}).values()),
        "mock_images":              list(_mock_store.get("images", {}).values()),
    })


@staff_member_required
@require_POST
def dev_tools_action(request):
    from core.models import ProcoreCredential, ProcoreMode
    from core.procore_mock_views import _mock_store

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    action = body.get("action")

    # ── Switch Procore mode ────────────────────────────────
    if action == "switch_mode":
        mode = body.get("mode")
        if mode not in ("mock", "sandbox", "production"):
            return JsonResponse({"error": "Invalid mode."}, status=400)

        procore_mode      = ProcoreMode.get()
        procore_mode.mode = mode
        procore_mode.updated_by = request.user
        procore_mode.save(update_fields=["mode", "updated_by", "updated_at"])

        logger.info("DEV TOOLS: Procore mode switched to %s by %s",
                    mode, request.user.username)

        return JsonResponse({
            "success": True,
            "mode":    mode,
            "message": f"Procore mode switched to {mode.upper()}. "
                       f"{'Mock endpoints will be used for all pushes.' if mode == 'mock' else 'Reconnect Procore on each project setup page.'}"
        })

    # ── Update mock base URL ───────────────────────────────
    elif action == "update_mock_url":
        url = body.get("mock_base_url", "").strip()
        if not url:
            return JsonResponse({"error": "URL cannot be empty."}, status=400)

        procore_mode = ProcoreMode.get()
        procore_mode.mock_base_url = url
        procore_mode.save(update_fields=["mock_base_url", "updated_at"])

        return JsonResponse({
            "success": True,
            "message": f"Mock base URL updated to {url}",
        })

    # ── Clear mock store ───────────────────────────────────
    elif action == "clear_mock_store":
        count_m = len(_mock_store["meetings"])
        count_a = len(_mock_store["action_items"])
        _mock_store["meetings"].clear()
        _mock_store["action_items"].clear()

        logger.info("DEV TOOLS: Mock store cleared (%s meetings, %s items)", count_m, count_a)

        return JsonResponse({
            "success": True,
            "message": f"Cleared {count_m} meeting{'' if count_m == 1 else 's'} "
                       f"and {count_a} action item{'' if count_a == 1 else 's'}.",
        })

    # ── Reset project Procore connection ───────────────────
    elif action == "reset_connection":
        project_id = body.get("project_id")
        try:
            cred = ProcoreCredential.objects.get(
                project_id=project_id,
                project__owner=request.user,
            )
            cred.is_connected  = False
            cred.access_token  = ''
            cred.refresh_token = ''
            cred.save(update_fields=["is_connected", "access_token", "refresh_token"])
            return JsonResponse({
                "success": True,
                "message": "Connection reset. Go to Procore Setup to reconnect.",
            })
        except ProcoreCredential.DoesNotExist:
            return JsonResponse({"error": "No credentials found."}, status=404)

    # ── Switch environment for a project ──────────────────
    elif action == "switch_environment":
        project_id  = body.get("project_id")
        environment = body.get("environment")

        if environment not in ("sandbox", "production"):
            return JsonResponse({"error": "Invalid environment."}, status=400)

        try:
            cred = ProcoreCredential.objects.get(
                project_id=project_id,
                project__owner=request.user,
            )
            cred.environment   = environment
            cred.is_connected  = False
            cred.access_token  = ''
            cred.refresh_token = ''
            cred.save(update_fields=[
                "environment", "is_connected",
                "access_token", "refresh_token",
            ])
            return JsonResponse({
                "success":     True,
                "environment": environment,
                "message":     f"Switched to {environment}. Reconnect on the setup page.",
            })
        except ProcoreCredential.DoesNotExist:
            return JsonResponse({"error": "No credentials found."}, status=404)

    return JsonResponse({"error": f"Unknown action: {action}"}, status=400)


def home_view(request):
    return render(request, 'core/home.html')

def about_view(request):
    return render(request, 'core/about.html')

def subscribe_view(request):
    if request.method == 'POST':
        email= request.POST.get('email')
        if email:
            try:
                send_mail(name, "A client with email: {} has just subscribed to GrowEasy marketing Email".format(email),settings.EMAIL_HOST_USER, ['daniel@groweasyanalytics.com'])
                return render(request, "core/thankyou.html")
            except BadHeaderError:
                return render(request, 'core/error.html')
        else:
            return HttpResponse('Please, fill the form correctly')
    return HttpResponse('Subscribe')


def contact_view(request):
    if request.method == 'POST':
        firstName= request.POST.get('firstName')
        lastName= request.POST.get('lastName')
        email= request.POST.get('email') 
        subject= request.POST.get('subject')
        message= request.POST.get('message')
        if email and firstName and message:
            try:
                send_mail(firstName, "A client with name: {} has just sent a message on your site with email: {} and message: {}. subject{}".format(firstName, email, message, subject),settings.EMAIL_HOST_USER, ['daniel@groweasyanalytics.com'])
                return render(request, "core/thankyou.html")
            except BadHeaderError:
                return render(request, 'core/error.html')
        else:
            return HttpResponse('Please, fill the form correctly')
    return render(request, 'core/contact.html')


def login_view(request):
    """User login"""
    if request.user.is_authenticated:
        return redirect('risk_overview')
    
    if request.method == 'POST':
        username = request.POST['username']
        password = request.POST['password']
        user = authenticate(request, username=username, password=password)
        
        if user:
            login(request, user)
            next_url = request.POST.get('next') or request.GET.get('next') or '/risk/'
            return redirect(next_url)
        
        return render(request, 'core/login.html', {'error': 'Invalid credentials'})
    
    # Pass 'next' to template context so it can be embedded in the form
    return render(request, 'core/login.html', {
        'next': request.GET.get('next', '/risk/')
    })


@login_required(login_url='login')
def logoutuser(request):
    '''
    client_username= request.user.username
    client_email= request.user.email
    template= render_to_string('core/logoutMail.html', {'name':client_username})
    plain_message= strip_tags(template)
    emailmessage= EmailMultiAlternatives(
    'Logout Notification',
    plain_message,
    settings.EMAIL_HOST_USER,
    [client_email],

    )
    emailmessage.attach_alternative(template, 'text/html')
    #emailmessage.send()
    '''
    logout(request)
    return redirect('login')