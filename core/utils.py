# core/utils.py
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from transformers import pipeline
import openai
from celery import shared_task
from googleapiclient.discovery import build
from google.oauth2 import service_account
from sklearn.linear_model import LinearRegression
import numpy as np


# Initialize sentiment analysis (runs once, downloads model)
sentiment_analyzer = pipeline("sentiment-analysis", model="distilbert-base-uncased-finetuned-sst-2-english")

# OpenAI setup (optional, requires API key)
openai.api_key = "your-openai-api-key"  # Store in environment variables for production

def analyze_sentiment(message):
    result = sentiment_analyzer(message)
    return result[0]['label'], result[0]['score']  # e.g., 'POSITIVE', 0.98

@shared_task
def generate_summary(messages):
    response = openai.ChatCompletion.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": f"Summarize these WhatsApp messages: {messages}"}],
        max_tokens=100
    )
    return response.choices[0].message.content


def read_google_sheet():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
    client = gspread.authorize(creds)
    sheet = client.open("Pros and cons").sheet1
    #headers = sheet.row_values(1) # Optional to return only headers
    data = sheet.get_all_records()  # Fetch all data, not just headers
    return data

def predict_trend(data, column='sales'):  # Assumes Sheets has a 'sales' column
    if not data or column not in data[0]:
        return None
    X = np.array([[i] for i in range(len(data))])
    y = np.array([row[column] for row in data])
    model = LinearRegression().fit(X, y)
    return float(model.predict([[len(data)]])[0])  # Next period prediction

def read_google_drive(folder_id):
    creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', ['https://www.googleapis.com/auth/drive'])
    service = build('drive', 'v3', credentials=creds)
    results = service.files().list(q=f"'{folder_id}' in parents", fields="files(id, name, mimeType)").execute()
    return results.get('files', [])

from slack_sdk import WebClient

def read_slack_channel(token, channel_id):
    client = WebClient(token=token)
    response = client.conversations_history(channel=channel_id, limit=100)
    return response['messages']

from hubspot import HubSpot
def read_hubspot_contacts(api_key):
    hubspot = HubSpot(api_key=api_key)
    contacts = hubspot.crm.contacts.get_all()
    return [contact.to_dict() for contact in contacts]







