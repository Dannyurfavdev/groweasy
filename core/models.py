
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

class Project(models.Model):
    """Construction project/job site"""
    name = models.CharField(max_length=255)
    address = models.TextField(blank=True)
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return self.name

class ProjectContact(models.Model):
    """Map phone numbers to projects"""
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='contacts')
    phone_number = models.CharField(max_length=20)  # e.g., "+2347068392922"
    contact_name = models.CharField(max_length=100)  # e.g., "Mike (Superintendent)"
    role = models.CharField(max_length=50, blank=True)  # e.g., "Superintendent", "Foreman"
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    
    class Meta:
        unique_together = ['phone_number']  # One phone can only be in one project
        ordering = ['contact_name']
    
    def __str__(self):
        return f"{self.contact_name} ({self.phone_number}) → {self.project.name}"


class MeetingRecord(models.Model):
    """
    Stores an uploaded meeting transcript and its extraction status.
    One MeetingRecord per meeting upload — linked to a specific project.
    """

    class Status(models.TextChoices):
        PENDING    = "PENDING",   "Pending Extraction"
        EXTRACTING = "EXTRACTING","Extracting..."
        DRAFT      = "DRAFT",     "Draft – Awaiting Review"
        APPROVED   = "APPROVED",  "Approved"
        PUSHED     = "PUSHED",    "Pushed to Procore"
        FAILED     = "FAILED",    "Extraction Failed"

    project         = models.ForeignKey("Project", on_delete=models.CASCADE, related_name="meetings")
    title           = models.CharField(max_length=255, blank=True, help_text="Auto-generated if blank")
    meeting_date    = models.DateField(null=True, blank=True, help_text="Extracted from transcript or set by PM")
    transcript_text = models.TextField()
    raw_ai_json     = models.JSONField(null=True, blank=True, help_text="Raw GPT-4 response for debugging")
    status          = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    uploaded_by     = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="uploaded_meetings")
    error_message   = models.TextField(blank=True, help_text="Populated if extraction fails")
    created_at      = models.DateTimeField(auto_now_add=True)
    updated_at      = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title or 'Untitled Meeting'} – {self.project.name}"

    @property
    def total_items(self):
        return (
            self.action_items.count()
            + self.decisions.count()
            + self.blockers.count()
        )


class ActionItem(models.Model):
    """A task extracted from the meeting, assigned to a contact."""

    class Status(models.TextChoices):
        OPEN        = "OPEN",       "Open"
        IN_PROGRESS = "IN_PROGRESS","In Progress"
        DONE        = "DONE",       "Done"
        CANCELLED   = "CANCELLED",  "Cancelled"

    meeting          = models.ForeignKey(MeetingRecord, on_delete=models.CASCADE, related_name="action_items")
    task_description = models.TextField()
    owner            = models.ForeignKey(
        "ProjectContact", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="action_items",
        help_text="Resolved from transcript name → project contact"
    )
    owner_raw_name   = models.CharField(max_length=100, blank=True, help_text="Name as it appeared in transcript")
    due_date         = models.DateField(null=True, blank=True)
    status           = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    procore_record_id = models.CharField(max_length=100, blank=True, help_text="Procore ID after push")
    notified_at      = models.DateTimeField(null=True, blank=True, help_text="When WhatsApp notification was sent")
    created_at       = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["due_date", "created_at"]

    def __str__(self):
        return f"{self.task_description[:60]} → {self.owner_raw_name or 'Unassigned'}"


class Decision(models.Model):
    """A decision recorded during the meeting."""

    meeting     = models.ForeignKey(MeetingRecord, on_delete=models.CASCADE, related_name="decisions")
    description = models.TextField()
    made_by     = models.ForeignKey(
        "ProjectContact", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="decisions_made"
    )
    made_by_raw_name = models.CharField(max_length=100, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.description[:80]


class Blocker(models.Model):
    """A blocker or risk item raised during the meeting."""

    class Severity(models.TextChoices):
        LOW    = "LOW",    "Low"
        MEDIUM = "MEDIUM", "Medium"
        HIGH   = "HIGH",   "High"

    meeting      = models.ForeignKey(MeetingRecord, on_delete=models.CASCADE, related_name="blockers")
    description  = models.TextField()
    blocking_who = models.CharField(max_length=255, blank=True)
    severity     = models.CharField(max_length=10, choices=Severity.choices, default=Severity.MEDIUM)
    resolved     = models.BooleanField(default=False)
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-severity", "created_at"]

    def __str__(self):
        return f"[{self.severity}] {self.description[:60]}"


class Message(models.Model):
    """WhatsApp messages with AI analysis"""
    SENTIMENT_CHOICES = [
        ('POSITIVE', 'Positive'),
        ('NEGATIVE', 'Negative'),
        ('NEUTRAL', 'Neutral'),
    ]
    
    project = models.ForeignKey(Project, on_delete=models.CASCADE, null=True, blank=True)
    sender = models.CharField(max_length=255)
    sender_name = models.CharField(max_length=255, blank=True)
    body = models.TextField()
    sentiment = models.CharField(max_length=10, choices=SENTIMENT_CHOICES, null=True)
    score = models.FloatField(null=True)
    
    # AI-extracted fields
    trade_detected = models.CharField(max_length=50, blank=True, default='')  # e.g., "Electrical", "Plumbing"
    location_detected = models.CharField(max_length=100, blank=True, default='')
    is_delay = models.BooleanField(default=False)
    delay_reason = models.CharField(max_length=255, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    processed = models.BooleanField(default=False)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.sender_name}: {self.body[:50]}"


class File(models.Model):
    """Photos, PDFs, documents from WhatsApp/Drive"""
    FILE_TYPE_CHOICES = [
        ('PHOTO', 'Photo'),
        ('PDF', 'PDF Document'),
        ('VOICE', 'Voice Note'),
        ('OTHER', 'Other'),
    ]
    
    SOURCE_CHOICES = [
        ('WHATSAPP', 'WhatsApp'),
        ('GDRIVE', 'Google Drive'),
        ('MANUAL', 'Manual Upload'),
    ]
    
    project = models.ForeignKey(Project, on_delete=models.CASCADE, null=True, blank=True)
    message = models.ForeignKey(Message, on_delete=models.SET_NULL, null=True, blank=True, related_name='files')
    
    name = models.CharField(max_length=255)
    file_type = models.CharField(max_length=10, choices=FILE_TYPE_CHOICES)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES)
    file_path = models.CharField(max_length=500)  # Local or cloud URL
    
    # AI-extracted metadata
    trade_tag = models.CharField(max_length=50, blank=True)
    location_tag = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)  # AI-generated description
    transcription = models.TextField(blank=True)  # For voice notes
    
    uploaded_at = models.DateTimeField(auto_now_add=True)
    processed = models.BooleanField(default=False)
    file = models.FileField(upload_to='whatsapp_media/', null=True, blank=True)
    
    class Meta:
        ordering = ['-uploaded_at']
    
    def __str__(self):
        return f"{self.name} ({self.file_type})"


class DataSource(models.Model):
    """Google Sheets, Drive folders configuration"""
    SOURCE_TYPE_CHOICES = [
        ('GSHEET', 'Google Sheet'),
        ('GDRIVE', 'Google Drive Folder'),
    ]
    
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    source_type = models.CharField(max_length=10, choices=SOURCE_TYPE_CHOICES)
    
    # Connection details
    sheet_id = models.CharField(max_length=255, blank=True)
    folder_id = models.CharField(max_length=255, blank=True)
    sheet_name = models.CharField(max_length=100, blank=True)  # Tab name
    
    # Metadata
    last_synced = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    
    def __str__(self):
        return f"{self.name} ({self.source_type})"


class SheetData(models.Model):
    """Cached Google Sheets data for quick dashboard loading"""
    source = models.ForeignKey(DataSource, on_delete=models.CASCADE)
    row_data = models.JSONField()  # Store entire row as JSON
    row_index = models.IntegerField()
    synced_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['row_index']
        unique_together = ['source', 'row_index']


class Alert(models.Model):
    """System-generated alerts (weather delays, missing docs, etc.)"""
    ALERT_TYPE_CHOICES = [
        ('DELAY', 'Delay Detected'),
        ('WEATHER', 'Weather Issue'),
        ('MISSING_DOC', 'Missing Document'),
        ('BUDGET', 'Budget Alert'),
    ]
    
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    alert_type = models.CharField(max_length=20, choices=ALERT_TYPE_CHOICES)
    title = models.CharField(max_length=255)
    description = models.TextField()
    
    related_message = models.ForeignKey(Message, on_delete=models.SET_NULL, null=True, blank=True)
    related_file = models.ForeignKey(File, on_delete=models.SET_NULL, null=True, blank=True)
    
    is_resolved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.alert_type}: {self.title}"



class RiskSnapshot(models.Model):
    """
    Stores a point-in-time risk assessment for a project.
    Generated by the calculate_project_risk Celery task.
    
    RISK LEVELS:
      healthy  = score 0-39   🟢
      medium   = score 40-69  🟡  
      high     = score 70-100 🔴
    """

    RISK_LEVELS = [
        ('healthy', 'Healthy'),
        ('medium', 'Medium Risk'),
        ('high', 'High Risk'),
    ]

    project = models.ForeignKey(
        'Project',  # references your existing Project model
        on_delete=models.CASCADE,
        related_name='risk_snapshots'
    )

    # --- THE SCORE ---
    # 0-100 integer. Weighted sum of the 3 signal types.
    score = models.IntegerField(default=0)
    risk_level = models.CharField(
        max_length=20,
        choices=RISK_LEVELS,
        default='healthy'
    )

    # --- RAW SIGNAL VALUES (stored for drill-down view) ---
    # These are what the Director sees when they click a project.
    negative_sentiment_pct = models.FloatField(default=0.0)   # e.g. 0.65 = 65%
    delay_count = models.IntegerField(default=0)               # e.g. 4 delays detected
    is_silent = models.BooleanField(default=False)             # True = no msgs in 24hrs
    message_count = models.IntegerField(default=0)             # total msgs analyzed

    # --- COMPRESSED SIGNALS (the "why") ---
    # A JSON list of human-readable signal strings.
    # e.g. ["Framing delay detected", "3 negative updates from Plumbing crew"]
    signals = models.JSONField(default=list)

    # --- METADATA ---
    # The time window we analyzed (last 48-72 hrs)
    window_hours = models.IntegerField(default=72)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at']  # newest first
        indexes = [
            models.Index(fields=['project', '-created_at']),
        ]

    def __str__(self):
        return f"{self.project.name} — {self.risk_level} ({self.score}/100) @ {self.created_at:%Y-%m-%d %H:%M}"

    @property
    def risk_emoji(self):
        return {'healthy': '🟢', 'medium': '🟡', 'high': '🔴'}.get(self.risk_level, '⚪')

    @property
    def risk_color(self):
        """Bootstrap color class for UI badges"""
        return {
            'healthy': 'success',
            'medium': 'warning',
            'high': 'danger'
        }.get(self.risk_level, 'secondary')



