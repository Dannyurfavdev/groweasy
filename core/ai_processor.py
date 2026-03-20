# core/ai_processor.py - GrowEasy Production Version
# Requires: pip install openai transformers torch
# Set OPENAI_API_KEY in your Django settings or environment variables

import json
from openai import OpenAI
from django.conf import settings
from transformers import pipeline

# ---------------------------------------------------------------------------
# Initialisation — runs once at startup
# ---------------------------------------------------------------------------

# Sentiment analyser (local, free, no API needed)
try:
    sentiment_analyzer = pipeline(
        "sentiment-analysis",
        model="distilbert-base-uncased-finetuned-sst-2-english"
    )
except Exception as e:
    print(f"Warning: Could not load sentiment analyser: {e}")
    sentiment_analyzer = None

# OpenAI client — key is pulled from Django settings
client = OpenAI(api_key=getattr(settings, 'OPENAI_API_KEY', ''))


# ---------------------------------------------------------------------------
# 1. Sentiment Analysis
# ---------------------------------------------------------------------------

def analyze_sentiment(message_text):
    """
    Returns (label, score) where label is 'POSITIVE', 'NEGATIVE', or 'NEUTRAL'.
    Uses a construction-aware keyword layer on top of the transformer model.
    """
    positive_keywords = [
        'complete', 'completed', 'done', 'finished', 'ready', 'passed',
        'approved', 'good', 'great', 'perfect', 'success', 'excellent',
        'on schedule', 'ahead', 'progress', 'installed', 'delivered'
    ]
    negative_keywords = [
        'delay', 'delayed', 'problem', 'issue', 'rain', 'weather',
        'behind', 'late', 'waiting', 'failed', 'reject', 'unsafe',
        'postpone', 'reschedule', 'cancel', 'shortage', 'missing'
    ]

    text_lower = message_text.lower()
    pos_count = sum(1 for w in positive_keywords if w in text_lower)
    neg_count = sum(1 for w in negative_keywords if w in text_lower)

    transformer_label = None
    transformer_score = 0.5

    if sentiment_analyzer:
        try:
            result = sentiment_analyzer(message_text[:512])
            transformer_label = result[0]['label']
            transformer_score = result[0]['score']
        except Exception as e:
            print(f"Transformer sentiment error: {e}")

    # Keyword layer takes priority for construction-specific text
    if pos_count > neg_count:
        score = transformer_score if transformer_label == 'POSITIVE' else 0.85
        return 'POSITIVE', max(0.85, score)
    elif neg_count > pos_count:
        score = transformer_score if transformer_label == 'NEGATIVE' else 0.85
        return 'NEGATIVE', max(0.85, score)
    else:
        if transformer_label:
            return transformer_label, transformer_score
        return 'NEUTRAL', 0.70


# ---------------------------------------------------------------------------
# 2. Construction Intelligence Extraction
# ---------------------------------------------------------------------------

def extract_construction_intel(message_text):
    """
    Uses GPT-4o-mini to extract trade, location, delay status, and a summary.
    Falls back to keyword matching if the API call fails.
    Returns a dict with keys: trade, location, is_delay, delay_reason, summary.
    """
    prompt = f"""Analyze this construction site message and extract:
1. Trade/discipline (Electrical, Plumbing, HVAC, Concrete, Framing, etc.)
2. Location on site (if mentioned)
3. Is this a delay? (yes/no)
4. If delay, what is the reason?
5. One-sentence summary

Message: "{message_text}"

Respond ONLY with valid JSON (no markdown fences):
{{
  "trade": "trade name or empty string",
  "location": "location or empty string",
  "is_delay": true or false,
  "delay_reason": "reason or empty string",
  "summary": "one sentence summary"
}}"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150,
            temperature=0.3
        )
        result_text = response.choices[0].message.content.strip()
        # Strip markdown fences if the model adds them despite instructions
        result_text = result_text.replace('```json', '').replace('```', '').strip()
        data = json.loads(result_text)

        return {
            "trade":        data.get('trade') or '',
            "location":     data.get('location') or '',
            "is_delay":     data.get('is_delay', False),
            "delay_reason": data.get('delay_reason') or '',
            "summary":      data.get('summary') or message_text[:100]
        }

    except Exception as e:
        print(f"AI extraction error: {e}")
        return {
            "trade":        '',
            "location":     '',
            "is_delay":     False,
            "delay_reason": '',
            "summary":      message_text[:100]
        }


# ---------------------------------------------------------------------------
# 3. Voice Note Transcription
# ---------------------------------------------------------------------------

def transcribe_voice_note(audio_file_path):
    """
    Transcribes an audio file using OpenAI Whisper.
    Supports mp3, mp4, mpeg, mpga, m4a, wav, webm.
    audio_file_path must be a local file path (download WhatsApp voice notes first).
    """
    try:
        with open(audio_file_path, 'rb') as audio_file:
            response = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file
            )
        return response.text
    except Exception as e:
        print(f"Transcription error: {e}")
        return ""


# ---------------------------------------------------------------------------
# 4. Image Description
# ---------------------------------------------------------------------------

def describe_image(image_url_or_path):
    """
    Generates a construction-focused description of a photo using GPT-4o vision.
    Pass a publicly accessible URL or a base64-encoded data URI.

    For local files, convert to base64 first:
        import base64
        with open(path, 'rb') as f:
            b64 = base64.b64encode(f.read()).decode('utf-8')
        url = f"data:image/jpeg;base64,{b64}"
        describe_image(url)
    """
    try:
        response = client.chat.completions.create(
            model="gpt-4o",          # gpt-4o handles vision natively; no -vision-preview needed
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Describe this construction site image. Identify: "
                                "trade/work type, location if visible, "
                                "quality/completion status, and any visible issues."
                            )
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": image_url_or_path}
                        }
                    ]
                }
            ],
            max_tokens=200
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"Image description error: {e}")
        return "Photo uploaded"


# ---------------------------------------------------------------------------
# 5. Weather Delay Detection
# ---------------------------------------------------------------------------

def detect_weather_delay(message_text):
    """
    Quick keyword check for weather-related delays.
    Returns True if a weather delay is detected.
    """
    weather_keywords = [
        'rain', 'raining', 'rainy', 'weather', 'storm', 'stormy',
        'snow', 'wind', 'windy', 'flood', 'wet', 'lightning', 'thunder'
    ]
    text_lower = message_text.lower()
    return any(keyword in text_lower for keyword in weather_keywords)


# ---------------------------------------------------------------------------
# 6. Related Document Suggestions
# ---------------------------------------------------------------------------

def suggest_related_documents(message_text, available_docs):
    """
    Uses GPT-4o-mini to identify which Google Drive documents are relevant
    to a given message. Returns up to 3 matching doc objects.
    available_docs: list of dicts with at least 'name' and 'type' keys.
    """
    if not available_docs:
        return []

    doc_list = "\n".join(
        [f"- {doc['name']} ({doc['type']})" for doc in available_docs[:20]]
    )

    prompt = f"""Given this construction site message:
"{message_text}"

Which of these documents are most relevant? Return up to 3 document names.
{doc_list}

Respond with a JSON array of document names only, e.g. ["doc1.pdf", "doc2.xlsx"]
No markdown fences."""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=100,
            temperature=0.2
        )
        result_text = response.choices[0].message.content.strip()
        result_text = result_text.replace('```json', '').replace('```', '').strip()
        suggested_names = json.loads(result_text)

        return [doc for doc in available_docs if doc['name'] in suggested_names]

    except Exception as e:
        print(f"Document suggestion error: {e}")
        return []


# ---------------------------------------------------------------------------
# 7. Dashboard Executive Summary
# ---------------------------------------------------------------------------

def generate_dashboard_summary(recent_messages, sheet_data):
    """
    Generates a 2-3 sentence executive summary of current project status.
    recent_messages: list of dicts with a 'body' key.
    sheet_data: list of rows from the connected Google Sheet.
    """
    if not recent_messages:
        return "No recent activity."

    message_texts = "\n".join(
        [f"- {msg['body']}" for msg in recent_messages[:10]]
    )

    prompt = f"""Summarize the current construction project status based on the following data.

Recent site messages:
{message_texts}

Budget/schedule data: {len(sheet_data)} rows tracked in Google Sheets.

Write a 2-3 sentence executive summary covering:
- Overall progress
- Any delays or issues
- Key metrics or next steps"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"Summary generation error: {e}")
        return "Recent activity tracked. Check dashboard for details."




