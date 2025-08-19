import os
import re
import time
import pickle
import base64
import dateparser
import unicodedata
import logging
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import google.generativeai as genai

from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_session import Session

# Import existing calendar functions
from calenderinternal import (
    authenticate_services, correct_schedule_spelling, extract_delete_details, get_event_by_name, is_schedule_intent,
    is_update_intent, is_delete_intent, extract_event_details,
    parse_datetime, send_invitation, wait_for_acceptance, delete_event, prompt_for_deletion_details, extract_update_details,
    check_participant_calendar_conflicts, send_conflict_notification, send_enhanced_email, handle_update_workflow,
    is_update_intent,
    extract_update_details,
    suggest_similar_events,
    format_suggestions_message
)

# Load environment variables
load_dotenv()

# Initialize Gemini with retry mechanism and error handling
def initialize_gemini():
    try:
        if os.getenv("GEMINI_API_KEY"):
            genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
            model = genai.GenerativeModel('gemini-1.5-flash')
            return model
        else:
            print("⚠️ GEMINI_API_KEY not found. AI features will use fallback methods.")
            return None
    except Exception as e:
        print(f"⚠️ Error initializing Gemini: {e}. Using fallback methods.")
        return None

model = initialize_gemini()

# Flask App Configuration
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "your-secret-key-here")
app.config["SESSION_TYPE"] = "filesystem"
Session(app)

# Initialize services
try:
    services = authenticate_services()
    print("✅ Services authenticated successfully")
except Exception as e:
    print(f"❌ Error authenticating services: {e}")
    services = None

REQUIRED_FIELDS = ["participant_email", "event_name", "event_date", "event_time"]

# Fixed field mapping for updates
UPDATE_REQUIRED_FIELDS = ["event_name", "new_date", "new_time"]

# Enhanced configuration for real-time applications
EMAIL_CONFIG = {
    'default_batch_size': 20,  # Increased from 15 to 50
    'max_batch_size': 200,     # Maximum emails to fetch at once
    'refresh_interval': 300,   # 5 minutes refresh interval for real-time
    'cache_duration': 600      # 10 minutes cache duration
}

# ===== ENHANCED CALENDAR FUNCTIONS =====
def create_event_with_meeting_link(calendar_service, summary, start_time, end_time, participant_email):
    """Create calendar event with Google Meet link and send invitation"""
    try:
        # Convert datetime objects to proper ISO format strings
        if not isinstance(start_time, str):
            start_time = start_time.isoformat()
        if not isinstance(end_time, str):
            end_time = end_time.isoformat()
        
        event = {
            'summary': summary,
            'start': {
                'dateTime': start_time,
                'timeZone': 'UTC',
            },
            'end': {
                'dateTime': end_time,
                'timeZone': 'UTC',
            },
            'attendees': [
                {'email': participant_email},
                {'email': get_authenticated_user_email()}  # Add the organizer's email
            ],
            'conferenceData': {
                'createRequest': {
                    'requestId': f"meet-{int(time.time())}-{hash(summary) % 10000}",
                    'conferenceSolutionKey': {'type': 'hangoutsMeet'}
                }
            },
            'description': f'Meeting scheduled through AI Calendar Assistant.\n\nJoin the meeting using Google Meet.',
            'reminders': {
                'useDefault': False,
                'overrides': [
                    {'method': 'email', 'minutes': 24 * 60},
                    {'method': 'popup', 'minutes': 10},
                ],
            },
        }

        # Create the event with conference data and send updates to attendees
        created_event = calendar_service.events().insert(
            calendarId='primary',
            body=event,
            conferenceDataVersion=1,
            sendUpdates='all'
        ).execute()
        
        print(f"✅ Event created: {created_event.get('htmlLink')}")
        
        # Extract actual Google Meet link from created event
        actual_meeting_link = ""
        if 'conferenceData' in created_event and 'entryPoints' in created_event['conferenceData']:
            for entry_point in created_event['conferenceData']['entryPoints']:
                if entry_point['entryPointType'] == 'video':
                    actual_meeting_link = entry_point['uri']
                    print(f"🔗 Google Meet link: {actual_meeting_link}")
                    break
        
        return created_event, actual_meeting_link
        
    except Exception as error:
        print(f"❌ Error creating event: {error}")
        return None, None

def update_event_with_meeting_link(calendar_service, event, new_start_time, new_end_time):
    """Update existing event with new time and meeting link"""
    try:
        # Update event details
        event['start']['dateTime'] = new_start_time.isoformat()
        event['end']['dateTime'] = new_end_time.isoformat()
        
        # Update the event
        updated_event = calendar_service.events().update(
            calendarId='primary',
            eventId=event['id'],
            body=event,
            conferenceDataVersion=1,
            sendUpdates='all'
        ).execute()
        
        print(f"✅ Event updated successfully")
        return updated_event
        
    except Exception as error:
        print(f"❌ Error updating event: {error}")
        return None

# ===== ENHANCED EMAIL FUNCTIONS =====
def send_meeting_cancellation_email(gmail_service, participant_email, event_name, reason=""):
    """Send meeting cancellation email"""
    try:
        subject = f"❌ Meeting Cancelled: {event_name}"
        
        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                <div style="background: linear-gradient(135deg, #ef4444 0%, #dc2626 100%); color: white; padding: 30px; border-radius: 10px; text-align: center;">
                    <h1 style="margin: 0; font-size: 28px;">❌ Meeting Cancelled</h1>
                    <p style="margin: 10px 0 0 0; font-size: 16px; opacity: 0.9;">We regret to inform you</p>
                </div>
                
                <div style="background-color: #fef2f2; padding: 25px; border-radius: 8px; margin: 25px 0; border-left: 5px solid #dc2626;">
                    <p style="color: #991b1b; font-size: 16px; margin: 0 0 15px 0;">
                        We regret to inform you that the following meeting has been cancelled:
                    </p>
                    <h2 style="color: #dc2626; margin: 10px 0; font-size: 20px;">{event_name}</h2>
                    {f'<div style="background-color: #fee2e2; padding: 15px; border-radius: 6px; margin: 15px 0;"><p style="margin: 0; color: #991b1b;"><strong>Reason:</strong> {reason}</p></div>' if reason else ""}
                </div>
                
                <div style="margin-top: 40px; padding-top: 25px; border-top: 2px solid #e5e7eb; text-align: center;">
                    <p style="color: #6b7280; font-size: 14px; margin: 0;">
                        We apologize for any inconvenience this may cause.<br>
                        Please contact us if you need to reschedule or have any questions.
                    </p>
                </div>
            </div>
        </body>
        </html>
        """
        
        text_body = f"""
❌ MEETING CANCELLED: {event_name}

We regret to inform you that the following meeting has been cancelled:
{event_name}

{f'Reason: {reason}' if reason else ''}

We apologize for any inconvenience this may cause.
Please contact us if you need to reschedule or have any questions.
        """
        
        return send_enhanced_email(gmail_service, participant_email, subject, text_body, html_body)
        
    except Exception as e:
        print(f"❌ Error sending cancellation email: {e}")
        return False

# ===== ENHANCED EMAIL PROCESSING FUNCTIONS =====
def fetch_recent_emails(gmail_service, max_results=EMAIL_CONFIG['default_batch_size']):
    """Fetch recent emails from inbox with enhanced pagination"""
    try:
        # Clamp max_results to prevent excessive API calls
        max_results = min(max_results, EMAIL_CONFIG['max_batch_size'])
        
        # Get list of messages with pagination support
        results = gmail_service.users().messages().list(
            userId='me',
            labelIds=['INBOX'],
            maxResults=max_results
        ).execute()
        
        messages = results.get('messages', [])
        emails = []
        
        print(f"📧 Fetching {len(messages)} emails...")
        
        # Process messages in batches to avoid API rate limits
        batch_size = 10
        for i in range(0, len(messages), batch_size):
            batch = messages[i:i+batch_size]
            
            for message in batch:
                try:
                    msg = gmail_service.users().messages().get(
                        userId='me', 
                        id=message['id']
                    ).execute()
                    
                    email_data = parse_email_message(msg)
                    if email_data:
                        emails.append(email_data)
                        
                except Exception as e:
                    print(f"⚠️ Error fetching message {message['id']}: {e}")
                    continue
            
            # Small delay between batches to respect rate limits
            if i + batch_size < len(messages):
                time.sleep(0.1)
        
        print(f"✅ Successfully fetched {len(emails)} emails")
        return emails
        
    except Exception as error:
        print(f"❌ Error fetching emails: {error}")
        return []

def parse_email_message(message):
    """Extract email details from Gmail API response"""
    try:
        payload = message['payload']
        headers = payload.get('headers', [])
        
        # Extract headers
        subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'No Subject')
        sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Unknown')
        date = next((h['value'] for h in headers if h['name'] == 'Date'), '')
        
        # Extract body
        body = extract_email_body(payload)
        
        return {
            'id': message['id'],
            'subject': subject,
            'sender': sender,
            'date': date,
            'body': body,
            'snippet': message.get('snippet', ''),
            'thread_id': message['threadId'],
            'timestamp': time.time()  # Add timestamp for caching
        }
    except Exception as e:
        print(f"⚠️ Error parsing email message: {e}")
        return {
            'id': message.get('id', 'unknown'),
            'subject': 'Error parsing email',
            'sender': 'Unknown',
            'date': '',
            'body': '',
            'snippet': message.get('snippet', ''),
            'thread_id': message.get('threadId', ''),
            'timestamp': time.time()
        }

def extract_email_body(payload):
    """Extract email body from message payload"""
    body = ""
    
    try:
        if 'parts' in payload:
            for part in payload['parts']:
                if part['mimeType'] == 'text/plain' and part['body'].get('data'):
                    data = part['body']['data']
                    body = base64.urlsafe_b64decode(data).decode('utf-8')
                    break
        elif payload['body'].get('data'):
            data = payload['body']['data']
            body = base64.urlsafe_b64decode(data).decode('utf-8')
    except Exception as e:
        print(f"⚠️ Error extracting email body: {e}")
        body = ""
    
    return body

def categorize_email_fallback(email):
    """Fallback categorization when AI is unavailable"""
    subject = email.get('subject', '').lower()
    body = email.get('body', '').lower()
    snippet = email.get('snippet', '').lower()
    
    # Combine text for analysis
    text = f"{subject} {body} {snippet}".lower()
    
    # High urgency keywords
    high_urgency_keywords = [
        'urgent', 'asap', 'emergency', 'critical', 'deadline', 'immediate',
        'important', '!!!', 'priority', 'expires', 'due today'
    ]
    
    # Meeting keywords
    meeting_keywords = [
        'meeting', 'schedule', 'appointment', 'call', 'conference',
        'zoom', 'teams', 'meet', 'calendar', 'booking', 'available'
    ]
    
    # Task keywords
    task_keywords = [
        'task', 'todo', 'action', 'review', 'complete', 'deadline',
        'project', 'assignment', 'deliverable'
    ]
    
    # Spam indicators
    spam_keywords = [
        'unsubscribe', 'promotion', 'offer', 'deal', 'discount',
        'free', 'winner', 'congratulations', 'click here'
    ]
    
    # Determine urgency
    urgency = 'low'
    if any(keyword in text for keyword in high_urgency_keywords):
        urgency = 'high'
    elif any(keyword in text for keyword in meeting_keywords + task_keywords):
        urgency = 'medium'
    
    # Determine category
    category = 'information'
    is_meeting_request = False
    
    if any(keyword in text for keyword in spam_keywords):
        category = 'spam'
    elif any(keyword in text for keyword in meeting_keywords):
        category = 'meeting'
        is_meeting_request = True
    elif any(personal in email.get('sender', '').lower() for personal in ['gmail.com', 'yahoo.com', 'hotmail.com']):
        category = 'personal'
    
    action_required = (urgency == 'high' or category in ['meeting', 'task'])
    
    return {
        "urgency": urgency,
        "category": category,
        "action_required": action_required,
        "confidence": 0.7,  # Moderate confidence for rule-based approach
        "reason": f"Rule-based categorization: {category} with {urgency} urgency",
        "is_meeting_request": is_meeting_request
    }

def categorize_email_with_ai(email):
    """Use Gemini AI to categorize email by urgency and type with fallback"""
    # Always try fallback first to avoid quota issues
    fallback_result = categorize_email_fallback(email)
    
    if not model:
        print("⚠️ AI model unavailable, using rule-based categorization")
        return fallback_result
    
    try:
        prompt = f"""
        Analyze this email and categorize it. Respond ONLY with valid JSON format.
        
        Subject: {email['subject'][:200]}
        From: {email['sender'][:100]}
        Content: {email['snippet'][:300]}
        
        Return exactly this JSON structure with no additional text:
        {{
            "urgency": "high|medium|low",
            "category": "meeting|task|information|personal|spam",
            "action_required": true|false,
            "confidence": 0.8,
            "reason": "brief explanation",
            "is_meeting_request": true|false
        }}
        """
        
        # Add retry mechanism with exponential backoff
        max_retries = 2
        base_delay = 1
        
        for attempt in range(max_retries):
            try:
                response = model.generate_content(prompt)
                if response and hasattr(response, 'text') and response.text:
                    # Clean the response text
                    response_text = response.text.strip()
                    
                    # Extract JSON from the response
                    if response_text.startswith('```json'):
                        response_text = response_text[7:-3].strip()
                    elif response_text.startswith('```'):
                        response_text = response_text[3:-3].strip()
                    
                    # Try to parse JSON
                    result = json.loads(response_text)
                    
                    # Validate required fields
                    required_fields = ["urgency", "category", "action_required", "confidence", "reason", "is_meeting_request"]
                    if all(field in result for field in required_fields):
                        return result
                    else:
                        print(f"⚠️ AI response missing required fields, using fallback")
                        return fallback_result
                        
            except json.JSONDecodeError as e:
                print(f"⚠️ JSON parsing error (attempt {attempt + 1}): {e}")
                if attempt == max_retries - 1:
                    return fallback_result
                time.sleep(base_delay * (2 ** attempt))
                
            except Exception as e:
                error_str = str(e)
                if "429" in error_str or "quota" in error_str.lower():
                    print(f"⚠️ Rate limit hit, using fallback categorization")
                    return fallback_result
                elif "400" in error_str:
                    print(f"⚠️ Bad request, using fallback categorization")
                    return fallback_result
                else:
                    print(f"⚠️ AI error (attempt {attempt + 1}): {e}")
                    if attempt == max_retries - 1:
                        return fallback_result
                    time.sleep(base_delay * (2 ** attempt))
                    
        return fallback_result
        
    except Exception as error:
        print(f"⚠️ AI categorization failed: {error}")
        return fallback_result

def summarize_email_fallback(email):
    """Fallback summarization when AI is unavailable"""
    subject = email.get('subject', 'No Subject')
    snippet = email.get('snippet', '')
    sender = email.get('sender', 'Unknown')
    
    # Extract sender name
    sender_name = sender.split('<')[0].strip() if '<' in sender else sender.split('@')[0]
    
    # Create a simple summary
    if snippet:
        summary = f"Email from {sender_name}: {subject}. {snippet[:100]}{'...' if len(snippet) > 100 else ''}"
    else:
        summary = f"Email from {sender_name} with subject: {subject}"
    
    return summary

def summarize_email_with_ai(email):
    """Generate AI summary of email content with fallback"""
    # Use fallback first to avoid quota issues
    fallback_summary = summarize_email_fallback(email)
    
    if not model:
        return fallback_summary
    
    try:
        content_text = email.get('body', email.get('snippet', ''))[:500]
        if not content_text.strip():
            return fallback_summary
            
        prompt = f"""
        Summarize this email in 1-2 concise sentences. Focus on key points and action items.
        
        Subject: {email['subject'][:200]}
        From: {email['sender'][:100]}
        Content: {content_text}
        
        Provide only the summary, no additional formatting.
        """
        
        response = model.generate_content(prompt)
        if response and hasattr(response, 'text') and response.text:
            ai_summary = response.text.strip()
            if len(ai_summary) > 10 and len(ai_summary) < 500:
                return ai_summary
        
        return fallback_summary
        
    except Exception as error:
        error_str = str(error)
        if "429" in error_str or "quota" in error_str.lower():
            print(f"⚠️ AI summarization quota exceeded, using fallback")
        else:
            print(f"⚠️ AI summarization error: {error}")
        return fallback_summary

def process_emails_with_ai(emails):
    """Process multiple emails with AI categorization and summarization"""
    processed_emails = []
    meeting_requests = []
    
    print(f"📧 Processing {len(emails)} emails...")
    
    for i, email in enumerate(emails):
        try:
            print(f"Processing email {i+1}/{len(emails)}: {email.get('subject', 'No Subject')[:50]}...")
            
            # Add AI analysis with fallback
            ai_analysis = categorize_email_with_ai(email)
            ai_summary = summarize_email_with_ai(email)
            
            # Combine original email with AI insights
            processed_email = {
                **email,
                'ai_urgency': ai_analysis['urgency'],
                'ai_category': ai_analysis['category'],
                'action_required': ai_analysis['action_required'],
                'confidence': ai_analysis['confidence'],
                'ai_reason': ai_analysis['reason'],
                'ai_summary': ai_summary,
                'is_meeting_request': ai_analysis.get('is_meeting_request', False)
            }
            
            processed_emails.append(processed_email)
            
            # Collect meeting requests for automatic processing
            if processed_email['is_meeting_request']:
                meeting_requests.append(processed_email)
                
        except Exception as e:
            print(f"⚠️ Error processing email {i+1}: {e}")
            # Add email with basic categorization
            processed_email = {
                **email,
                'ai_urgency': 'medium',
                'ai_category': 'information',
                'action_required': False,
                'confidence': 0.3,
                'ai_reason': 'Error in processing',
                'ai_summary': f"Email from {email.get('sender', 'Unknown')}: {email.get('subject', 'No Subject')}",
                'is_meeting_request': False
            }
            processed_emails.append(processed_email)
    
    # Sort by urgency (high -> medium -> low)
    urgency_order = {'high': 0, 'medium': 1, 'low': 2}
    processed_emails.sort(key=lambda x: urgency_order.get(x['ai_urgency'], 1))
    
    print(f"✅ Processed {len(processed_emails)} emails, found {len(meeting_requests)} meeting requests")
    
    return processed_emails, meeting_requests

def extract_meeting_details_fallback(email):
    """Fallback meeting details extraction using patterns"""
    subject = email.get('subject', '')
    body = email.get('body', '')
    snippet = email.get('snippet', '')
    sender = email.get('sender', '')
    
    # Extract sender email
    sender_email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', sender)
    participant_email = sender_email_match.group(0) if sender_email_match else None
    
    # Use subject as event name by default
    event_name = subject.strip() if subject and 'Re:' not in subject else 'Meeting Request'
    
    # Look for dates in text
    text = f"{subject} {body} {snippet}".lower()
    
    # Common date patterns
    date_patterns = [
        r'tomorrow',
        r'today',
        r'next week',
        r'monday|tuesday|wednesday|thursday|friday|saturday|sunday',
        r'\d{1,2}[/-]\d{1,2}[/-]\d{2,4}',
        r'\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+\d{2,4}',
        r'(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+\d{1,2}(?:st|nd|rd|th)?'
    ]
    
    # Common time patterns
    time_patterns = [
        r'\d{1,2}:\d{2}\s*(?:am|pm)',
        r'\d{1,2}\s*(?:am|pm)',
        r'\d{1,2}:\d{2}\s*(?:am|pm)\s*(?:to|-)\s*\d{1,2}:\d{2}\s*(?:am|pm)',
        r'\d{1,2}\s*(?:am|pm)\s*(?:to|-)\s*\d{1,2}\s*(?:am|pm)'
    ]
    
    found_date = None
    found_time = None
    
    # Search for dates
    for pattern in date_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        if matches:
            found_date = matches[0]
            break
    
    # Search for times
    for pattern in time_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        if matches:
            found_time = matches[0]
            break
    
    # Try to parse the found date
    parsed_date = None
    if found_date:
        try:
            if found_date.lower() == 'tomorrow':
                tomorrow = datetime.now() + timedelta(days=1)
                parsed_date = tomorrow.strftime('%Y-%m-%d')
            elif found_date.lower() == 'today':
                today = datetime.now()
                parsed_date = today.strftime('%Y-%m-%d')
            else:
                parsed = dateparser.parse(found_date, settings={'PREFER_DATES_FROM': 'future'})
                if parsed and parsed.date() >= datetime.now().date():
                    parsed_date = parsed.strftime('%Y-%m-%d')
        except:
            pass
    
    has_complete_info = bool(participant_email and event_name and parsed_date and found_time)
    
    return {
        'participant_email': participant_email,
        'event_name': event_name,
        'event_date': parsed_date,
        'event_time': found_time,
        'has_complete_info': has_complete_info
    }

def extract_meeting_details_from_email(email):
    """Extract meeting details from email using AI with fallback"""
    # Try fallback first to avoid quota issues
    fallback_result = extract_meeting_details_fallback(email)
    
    if not model:
        return fallback_result
    
    try:
        content_text = email.get('body', email.get('snippet', ''))[:800]
        
        prompt = f"""
        Extract meeting details from this email. Return ONLY valid JSON format.
        
        Subject: {email['subject'][:200]}
        From: {email['sender'][:100]}
        Body: {content_text}
        
        Extract and return exactly this JSON structure:
        {{
            "participant_email": "sender email address or null",
            "event_name": "meeting title or null", 
            "event_date": "YYYY-MM-DD format or null",
            "event_time": "time range like '10:00 AM to 11:00 AM' or null",
            "has_complete_info": true|false
        }}
        
        Rules:
        - Extract sender email from sender field
        - Use future dates only
        - Set has_complete_info to true only if ALL fields have valid values
        """
        
        response = model.generate_content(prompt)
        if response and hasattr(response, 'text') and response.text:
            response_text = response.text.strip()
            
            # Clean JSON formatting
            if response_text.startswith('```json'):
                response_text = response_text[7:-3].strip()
            elif response_text.startswith('```'):
                response_text = response_text[3:-3].strip()
            
            details = json.loads(response_text)
            
            # Extract sender email if not properly extracted
            if not details.get('participant_email'):
                sender_email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', email['sender'])
                if sender_email_match:
                    details['participant_email'] = sender_email_match.group(0)
            
            # Validate the result has required fields
            required_fields = ['participant_email', 'event_name', 'event_date', 'event_time', 'has_complete_info']
            if all(field in details for field in required_fields):
                return details
            
        return fallback_result
        
    except Exception as error:
        error_str = str(error)
        if "429" in error_str or "quota" in error_str.lower():
            print(f"⚠️ Meeting extraction quota exceeded, using fallback")
        else:
            print(f"⚠️ Meeting extraction error: {error}")
        return fallback_result

# ===== ENHANCED MEETING TRACKING =====
def get_authenticated_user_email():
    """Get the email address of the authenticated user"""
    try:
        if services and 'gmail' in services:
            profile = services['gmail'].users().getProfile(userId='me').execute()
            return profile.get('emailAddress')
    except Exception as e:
        print(f"⚠️ Error getting user email: {e}")
    return None

def get_scheduled_meetings_from_calendar():
    """Fetch actual scheduled meetings from Google Calendar"""
    try:
        if not services or 'calendar' not in services:
            return []
        
        # Get current time and next 30 days
        now = datetime.utcnow()
        time_min = now.isoformat() + 'Z'
        time_max = (now + timedelta(days=30)).isoformat() + 'Z'
        
        # Fetch events from calendar
        events_result = services['calendar'].events().list(
            calendarId='primary',
            timeMin=time_min,
            timeMax=time_max,
            maxResults=50,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        user_email = get_authenticated_user_email()
        
        scheduled_meetings = []
        
        for event in events:
            # Check if this event has attendees (indicating it's a meeting)
            attendees = event.get('attendees', [])
            if not attendees:
                continue
            
            # Check if the authenticated user is the organizer
            organizer = event.get('organizer', {})
            creator = event.get('creator', {})
            
            is_user_organizer = (
                organizer.get('email', '').lower() == user_email.lower() if user_email else False or
                creator.get('email', '').lower() == user_email.lower() if user_email else False
            )
            
            if is_user_organizer:
                # Extract meeting details
                start_time = event.get('start', {})
                end_time = event.get('end', {})
                
                # Get meeting link from conference data
                meeting_link = ""
                if 'conferenceData' in event and 'entryPoints' in event['conferenceData']:
                    for entry_point in event['conferenceData']['entryPoints']:
                        if entry_point['entryPointType'] == 'video':
                            meeting_link = entry_point['uri']
                            break
                
                # Get participant emails
                participant_emails = [
                attendee['email']
                for attendee in attendees
                if not user_email or attendee['email'].lower() != user_email.lower()
            ]

                
                meeting_record = {
                    'id': f"cal_{event['id']}",
                    'event_name': event.get('summary', 'Untitled Meeting'),
                    'participant_emails': participant_emails,
                    'participant_email': participant_emails[0] if participant_emails else 'No participants',
                    'event_date': start_time.get('dateTime', start_time.get('date', '')).split('T')[0] if start_time else '',
                    'event_time': f"{start_time.get('dateTime', '').split('T')[1][:5] if start_time.get('dateTime') else 'All day'} - {end_time.get('dateTime', '').split('T')[1][:5] if end_time.get('dateTime') else 'All day'}",
                    'meeting_link': meeting_link,
                    'scheduled_at': event.get('created', ''),
                    'status': 'scheduled',
                    'calendar_event_id': event['id'],
                    'organizer': organizer.get('email', ''),
                    'attendee_count': len(attendees)
                }
                
                scheduled_meetings.append(meeting_record)
        
        print(f"✅ Found {len(scheduled_meetings)} scheduled meetings from calendar")
        return scheduled_meetings
        
    except Exception as e:
        print(f"⚠️ Error fetching scheduled meetings from calendar: {e}")
        return []

def track_scheduled_meeting(participant_email, event_name, event_date, event_time, meeting_link, calendar_event_id=None):
    """Track scheduled meetings for email reflection"""
    try:
        # Store in session for current tracking
        if 'scheduled_meetings' not in session:
            session['scheduled_meetings'] = []
        
        meeting_record = {
            'id': f"scheduled_{int(time.time())}_{hash(event_name) % 10000}",
            'participant_email': participant_email,
            'event_name': event_name,
            'event_date': event_date,
            'event_time': event_time,
            'meeting_link': meeting_link,
            'scheduled_at': datetime.now().isoformat(),
            'status': 'scheduled',
            'calendar_event_id': calendar_event_id
        }
        
        session['scheduled_meetings'].append(meeting_record)
        session.modified = True
        
        print(f"✅ Meeting tracked: {event_name} with {participant_email}")
        return meeting_record
        
    except Exception as e:
        print(f"⚠️ Error tracking meeting: {e}")
        return None

def get_all_scheduled_meetings():
    """Get all scheduled meetings from both session and calendar"""
    try:
        # Get meetings from session (recently scheduled)
        session_meetings = session.get('scheduled_meetings', [])
        
        # Get meetings from calendar (actual scheduled meetings)
        calendar_meetings = get_scheduled_meetings_from_calendar()
        
        # Combine and deduplicate
        all_meetings = []
        seen_ids = set()
        
        # Add calendar meetings first (more authoritative)
        for meeting in calendar_meetings:
            if meeting['id'] not in seen_ids:
                all_meetings.append(meeting)
                seen_ids.add(meeting['id'])
        
        # Add session meetings that aren't already in calendar
        for meeting in session_meetings:
            if meeting['id'] not in seen_ids:
                all_meetings.append(meeting)
                seen_ids.add(meeting['id'])
        
        # Sort by date and time
        all_meetings.sort(key=lambda x: (x.get('event_date', ''), x.get('event_time', '')))
        
        return all_meetings
        
    except Exception as e:
        print(f"⚠️ Error getting all scheduled meetings: {e}")
        return []

def get_scheduled_meetings_count():
    """Get count of all scheduled meetings"""
    return len(get_all_scheduled_meetings())

def simulate_meeting_emails():
    """Generate email representations of scheduled meetings"""
    scheduled_meetings = get_all_scheduled_meetings()
    simulated_emails = []
    
    for meeting in scheduled_meetings:
        # Create invitation email simulation
        invitation_email = {
            'id': f"sim_inv_{meeting['id']}",
            'subject': f"📅 Meeting Invitation: {meeting['event_name']}",
            'sender': f"AI Calendar System <noreply@calendar.ai>",
            'date': meeting.get('scheduled_at', datetime.now().isoformat()),
            'body': f"Meeting invitation sent for {meeting['event_name']} on {meeting['event_date']} at {meeting['event_time']}",
            'snippet': f"Meeting invitation: {meeting['event_name']} - {meeting['event_date']} {meeting['event_time']}",
            'thread_id': f"thread_{meeting['id']}_inv",
            'ai_urgency': 'high',
            'ai_category': 'meeting',
            'action_required': True,
            'confidence': 1.0,
            'ai_reason': 'Meeting invitation sent',
            'ai_summary': f"Invitation sent for {meeting['event_name']} meeting",
            'is_meeting_request': True,
            'meeting_status': 'invitation_sent',
            'timestamp': time.time()
        }
        
        # Create confirmation email simulation
        confirmation_email = {
            'id': f"sim_conf_{meeting['id']}",
            'subject': f"✅ Meeting Confirmed: {meeting['event_name']}",
            'sender': f"AI Calendar System <noreply@calendar.ai>",
            'date': meeting.get('scheduled_at', datetime.now().isoformat()),
            'body': f"Meeting confirmed for {meeting['event_name']} on {meeting['event_date']} at {meeting['event_time']}. Join link: {meeting.get('meeting_link', 'N/A')}",
            'snippet': f"Meeting confirmed: {meeting['event_name']} - Google Meet link provided",
            'thread_id': f"thread_{meeting['id']}_conf",
            'ai_urgency': 'high',
            'ai_category': 'meeting',
            'action_required': False,
            'confidence': 1.0,
            'ai_reason': 'Meeting confirmation sent',
            'ai_summary': f"Meeting {meeting['event_name']} confirmed and scheduled successfully",
            'is_meeting_request': False,
            'meeting_status': 'confirmed',
            'timestamp': time.time()
        }
        
        simulated_emails.extend([invitation_email, confirmation_email])
    
    return simulated_emails

# ===== UTILITY FUNCTIONS =====
def get_missing_field_prompt(current_data):
    """Get the next missing field prompt"""
    missing = [field for field in REQUIRED_FIELDS if field not in current_data or not current_data[field]]
    questions = {
        "participant_email": "What is the participant's email address?",
        "event_name": "What should be the event name?",
        "event_date": "When is the meeting? (e.g., today, tomorrow, August 15, or April 8 2025)",
        "event_time": "What time is the meeting? (e.g., 10:00 AM to 11:00 AM)"
    }
    if missing:
        field = missing[0]
        return field, f"Please specify the missing information: {questions[field]}"
    return None, None

# ===== FIXED UPDATE FUNCTIONS =====
def get_missing_update_field_prompt(current_data):
    """Get the next missing field prompt for updates"""
    missing = [field for field in UPDATE_REQUIRED_FIELDS if field not in current_data or not current_data[field]]
    questions = {
        "event_name": "What is the event name you want to update?",
        "new_date": "What is the new date? (e.g., today, tomorrow, August 15, or April 8 2025)",
        "new_time": "What is the new time? (e.g., 10:00 AM to 11:00 AM)"
    }
    if missing:
        field = missing[0]
        return field, f"Please specify: {questions[field]}"
    return None, None

def process_update_field_input(field, user_input, current_data):
    """Process user input for specific update field"""
    user_input = user_input.strip()
    
    if field == "event_name" and user_input:
        current_data[field] = user_input
        return True, "✅ Event name updated!"
        
    elif field == "new_date":
        parsed = dateparser.parse(user_input, settings={'PREFER_DATES_FROM': 'future'})
        if parsed and parsed.date() >= datetime.now().date():
            current_data[field] = parsed.strftime('%Y-%m-%d')
            return True, "✅ New date saved!"
        else:
            return False, "⚠️ Please enter a valid future date (e.g., 'tomorrow', 'April 15', 'next Monday')."
            
    elif field == "new_time":
        # Try to extract time using the existing function
        extracted_time = extract_event_details(user_input).get('event_time')
        if extracted_time:
            current_data[field] = extracted_time
            return True, "✅ New time saved!"
        else:
            return False, "⚠️ Please enter a valid time range (e.g., '2 PM to 3 PM', '10:00 AM - 11:00 AM')."
    
    return False, "⚠️ Invalid input for this field."

def is_email_processing_intent(message):
    """Detect if message is about email processing - only when explicitly requested"""
    email_keywords = [
        'check emails', 'show emails', 'process emails', 'email dashboard', 
        'my emails', 'recent emails', 'inbox messages', 'check my inbox',
        'show my inbox', 'process my emails', 'fetch emails', 'get emails'
    ]
    message_lower = message.lower().strip()
    
    # Check for explicit email processing requests
    for keyword in email_keywords:
        if keyword in message_lower:
            return True

    # Don't trigger on email addresses
    if re.match(r'^[\w\.-]+@[\w\.-]+\.\w+$', message_lower):
        return False
    return False

# Email validation function
def validate_email(email):
    """Validate email format"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

# Initialize AI chat with error handling
def initialize_chat():
    """Initialize Gemini chat with error handling"""
    try:
        if model:
            chat = model.start_chat(history=[])
            return chat
        else:
            print("⚠️ AI model unavailable, chat will use fallback responses")
            return None
    except Exception as e:
        print(f"❌ Error initializing chat: {e}")
        return None

# Initialize chat globally
chat = initialize_chat()

# ===== FLASK ROUTES =====

@app.route('/')
def index():
    """Main dashboard"""
    session.clear()
    return render_template('index.html')

@app.route('/emails')
def emails_dashboard():
    """Display email dashboard"""
    return render_template('emails.html')

@app.route('/calendar')
def calendar_dashboard():
    """Display calendar dashboard"""
    return render_template('calendar.html')

# ===== API ROUTES =====

@app.route('/api/emails', methods=['GET'])
def get_processed_emails():
    """API endpoint to fetch and process emails with enhanced batch size"""
    try:
        # Get batch size from request parameters
        batch_size = request.args.get('batch_size', EMAIL_CONFIG['default_batch_size'], type=int)
        batch_size = min(batch_size, EMAIL_CONFIG['max_batch_size'])  # Clamp to max
        
        if not services or 'gmail' not in services:
            return jsonify({
                'success': False,
                'error': 'Gmail service not available. Please check authentication.'
            }), 500

        print(f"📧 Starting email fetch and processing with batch size: {batch_size}...")
        
        # Fetch recent emails with specified batch size
        raw_emails = fetch_recent_emails(services['gmail'], max_results=batch_size)
        
        # Add simulated meeting emails to show scheduled meetings
        simulated_meeting_emails = simulate_meeting_emails()
        
        # Combine real emails with simulated meeting emails
        all_emails = simulated_meeting_emails + raw_emails
        
        if not all_emails:
            return jsonify({
                'success': True,
                'emails': [],
                'meeting_requests': [],
                'total_count': 0,
                'meeting_count': 0,
                'scheduled_meetings_count': 0,
                'batch_size': batch_size,
                'message': 'No emails found'
            })
        
        print(f"📧 Processing {len(raw_emails)} real emails + {len(simulated_meeting_emails)} simulated emails")
        
        # Process with AI (only process real emails, simulated ones are already processed)
        processed_real_emails, meeting_requests = process_emails_with_ai(raw_emails)
        
        # Combine processed real emails with simulated emails
        all_processed_emails = simulated_meeting_emails + processed_real_emails
        
        # Store in session for later use
        session['processed_emails'] = all_processed_emails
        session['meeting_requests'] = meeting_requests
        
        # Count different types
        scheduled_meetings_count = get_scheduled_meetings_count()
        meeting_count = len([e for e in all_processed_emails if e.get('is_meeting_request', False)])
        
        print(f"✅ Email processing complete: {len(all_processed_emails)} total, {meeting_count} meetings, {scheduled_meetings_count} scheduled")
        
        return jsonify({
            'success': True,
            'emails': all_processed_emails,
            'meeting_requests': meeting_requests,
            'total_count': len(all_processed_emails),
            'meeting_count': meeting_count,
            'scheduled_meetings_count': scheduled_meetings_count,
            'batch_size': batch_size,
            'real_emails_count': len(raw_emails),
            'simulated_emails_count': len(simulated_meeting_emails)
        })
        
    except Exception as error:
        print(f"❌ Error processing emails: {error}")
        return jsonify({
            'success': False,
            'error': str(error)
        }), 500

@app.route('/api/scheduled_meetings', methods=['GET'])
def get_scheduled_meetings():
    """API endpoint to get all scheduled meetings"""
    try:
        scheduled_meetings = get_all_scheduled_meetings()
        
        return jsonify({
            'success': True,
            'scheduled_meetings': scheduled_meetings,
            'count': len(scheduled_meetings)
        })
        
    except Exception as error:
        print(f"❌ Error fetching scheduled meetings: {error}")
        return jsonify({
            'success': False,
            'error': str(error)
        }), 500

@app.route('/api/email_config', methods=['GET', 'POST'])
def email_config():
    """API endpoint to get/set email configuration"""
    if request.method == 'GET':
        return jsonify({
            'success': True,
            'config': EMAIL_CONFIG
        })
    
    elif request.method == 'POST':
        try:
            data = request.json
            
            # Update batch size if provided
            if 'default_batch_size' in data:
                new_batch_size = min(int(data['default_batch_size']), EMAIL_CONFIG['max_batch_size'])
                EMAIL_CONFIG['default_batch_size'] = max(10, new_batch_size)  # Minimum 10
            
            # Update refresh interval if provided
            if 'refresh_interval' in data:
                EMAIL_CONFIG['refresh_interval'] = max(60, int(data['refresh_interval']))  # Minimum 1 minute
            
            return jsonify({
                'success': True,
                'config': EMAIL_CONFIG,
                'message': 'Configuration updated successfully'
            })
            
        except Exception as error:
            return jsonify({
                'success': False,
                'error': str(error)
            }), 400

@app.route('/chat', methods=['POST'])
def chat_route():
    """Enhanced chat route with FIXED meeting scheduling and update flow"""
    try:
        user_input = request.json.get("message", "").strip()
        
        if not user_input:
            return jsonify({"reply": "Please enter a message."})
            
        user_input = correct_schedule_spelling(user_input)

        if 'data' not in session:
            session['data'] = {}
        data = session['data']

        if 'messages' not in session:
            session['messages'] = []
        
        print(f"DEBUG - User input: '{user_input}'")
        print(f"DEBUG - Current session intent: {session.get('intent')}")
        print(f"DEBUG - Current data: {session.get('data', {})}")
        print(f"DEBUG - Waiting for: {session.get('waiting_for')}")

        # Check if user is asking about emails (only explicit requests)
        if is_email_processing_intent(user_input) and 'intent' not in session:
            try:
                if not services or 'gmail' not in services:
                    return jsonify({
                        'reply': '❌ Gmail service not available. Please check your authentication and try again.'
                    })

                # Get batch size from user input if specified
                batch_size = EMAIL_CONFIG['default_batch_size']
                if 'all emails' in user_input.lower() or 'all my emails' in user_input.lower():
                    batch_size = EMAIL_CONFIG['max_batch_size']
                elif any(num in user_input for num in ['50', '100', '200']):
                    # Extract number if user specifies
                    numbers = re.findall(r'\b(\d+)\b', user_input)
                    if numbers:
                        batch_size = min(int(numbers[0]), EMAIL_CONFIG['max_batch_size'])

                raw_emails = fetch_recent_emails(services['gmail'], max_results=batch_size)
                simulated_meeting_emails = simulate_meeting_emails()
                all_emails = simulated_meeting_emails + raw_emails
                
                if not all_emails:
                    return jsonify({
                        'reply': '📧 No emails found in your inbox.'
                    })
                
                processed_real_emails, meeting_requests = process_emails_with_ai(raw_emails)
                all_processed_emails = simulated_meeting_emails + processed_real_emails
                
                session['processed_emails'] = all_processed_emails
                session['meeting_requests'] = meeting_requests
                
                high_priority = len([e for e in all_processed_emails if e.get('ai_urgency') == 'high'])
                action_required = len([e for e in all_processed_emails if e.get('action_required')])
                meeting_count = len([e for e in all_processed_emails if e.get('is_meeting_request', False)])
                scheduled_count = get_scheduled_meetings_count()
                
                summary_msg = f"📧 **Email Summary (Batch: {len(all_processed_emails)}):**\n"
                summary_msg += f"• Total emails: {len(all_processed_emails)} ({len(raw_emails)} real + {len(simulated_meeting_emails)} scheduled)\n"
                summary_msg += f"• High priority: {high_priority}\n"
                summary_msg += f"• Action required: {action_required}\n"
                summary_msg += f"• Meeting requests: {len(meeting_requests)}\n"
                summary_msg += f"• Scheduled meetings: {scheduled_count}\n\n"
                
                if meeting_requests:
                    summary_msg += "🗓️ **Meeting requests detected!**\n"
                    for i, req in enumerate(meeting_requests[:3], 1):
                        summary_msg += f"{i}. {req['subject']} (from {req['sender']})\n"
                    summary_msg += "\n💬 Would you like me to process these meeting requests automatically?"
                else:
                    summary_msg += "✅ Check the Email Dashboard for detailed view!"
                
                return jsonify({
                    "reply": summary_msg,
                    "action": "show_emails" if not meeting_requests else "show_meetings",
                    "data": {
                        "total_emails": len(all_processed_emails),
                        "meeting_requests": len(meeting_requests),
                        "scheduled_meetings": scheduled_count,
                        "batch_size": batch_size
                    }
                })
                
            except Exception as error:
                return jsonify({"reply": f"❌ Error processing emails: {str(error)}"})

        # Initial intent detection (only if no current intent)
        if 'intent' not in session:
            if is_schedule_intent(user_input):
                session['intent'] = 'schedule'
                extracted = extract_event_details(user_input)
                session['data'] = extracted
                session.modified = True
                print(f"DEBUG - Schedule intent detected, extracted data: {extracted}")
            elif is_update_intent(user_input):
                session['intent'] = 'update'
                session['update_text'] = user_input
                extracted = extract_update_details(user_input)
                # Map the fields correctly for the update flow
                session['data'] = {
                    'event_name': extracted.get('event_name'),
                    'new_date': extracted.get('event_date'),  # Map event_date to new_date
                    'new_time': extracted.get('event_time')   # Map event_time to new_time
                }
                session.modified = True
                print(f"DEBUG - Update intent detected, mapped data: {session['data']}")
            elif is_delete_intent(user_input):
                session['intent'] = 'delete'
                session['delete_text'] = user_input
                session['data'] = extract_delete_details(user_input)
                session.modified = True
            else:
                # Handle meeting request processing confirmation
                if "yes" in user_input.lower() and session.get('meeting_requests'):
                    return process_meeting_requests_from_chat()
                
                # Default AI response with fallback
                try:
                    if chat:
                        reply = chat.send_message(user_input).text
                        return jsonify({"reply": reply})
                    else:
                        # Fallback responses when AI is unavailable
                        fallback_responses = {
                            'hello': "Hello! I'm your AI Calendar Assistant. I can help you schedule meetings, update events, delete appointments, or check your emails. What would you like to do?",
                            'help': "I can help you with:\n• Schedule a meeting: 'Schedule a meeting with john@example.com tomorrow at 2 PM'\n• Update an event: 'Update the team meeting to Friday at 3 PM'\n• Delete an event: 'Delete the project review meeting'\n• Check emails: 'Check my emails' or 'Show all emails'\n• Get scheduled meetings: 'Show my scheduled meetings'\n\nWhat would you like to do?",
                            'thanks': "You're welcome! Is there anything else I can help you with?",
                            'bye': "Goodbye! Feel free to return whenever you need help with your calendar or emails."
                        }
                        
                        user_lower = user_input.lower()
                        for key, response in fallback_responses.items():
                            if key in user_lower:
                                return jsonify({"reply": response})
                        
                        return jsonify({"reply": "I can help you schedule meetings, update events, delete appointments, or check your emails. Please let me know what you'd like to do, or say 'help' for more information."})
                        
                except Exception as e:
                    return jsonify({"reply": "I can help you schedule meetings, update events, delete appointments, or check your emails. Please let me know what you'd like to do!"})

        intent = session['intent']
        print(f"DEBUG - Processing intent: {intent}")

        # ===== FIXED SCHEDULE FLOW =====
        if intent == 'schedule':
            # Handle user input for the waiting_for field
            if 'waiting_for' in session:
                field = session['waiting_for']
                if field == "participant_email" and validate_email(user_input):
                    data[field] = user_input
                elif field == "event_name" and user_input:
                    data[field] = user_input
                elif field == "event_date":
                    parsed = dateparser.parse(user_input, settings={'PREFER_DATES_FROM': 'future'})
                    if parsed and parsed.date() >= datetime.now().date():
                        data[field] = parsed.strftime('%Y-%m-%d')
                    else:
                        return jsonify({"reply": "⚠️ Please enter a valid future date."})
                elif field == "event_time":
                    extracted_time = extract_event_details(user_input).get('event_time')
                    if extracted_time:
                        data[field] = extracted_time
                    else:
                        return jsonify({"reply": "⚠️ Please enter a valid time (e.g., '2 PM', '10 AM to 11 AM')."})
                
                session['data'] = data
                session.pop('waiting_for', None)
                session.modified = True
            
            # Check for missing fields and prompt for them
            field, prompt = get_missing_field_prompt(session['data'])
            if field:
                session['waiting_for'] = field
                session.modified = True
                print(f"DEBUG - Missing field: {field}, prompting user")
                return jsonify({"reply": prompt})

            # All fields are present, proceed with scheduling
            details = session['data']
            print(f"DEBUG - All fields present, proceeding with scheduling: {details}")
            
            # Validate services
            if not services or 'gmail' not in services or 'calendar' not in services:
                session.clear()
                return jsonify({"reply": "❌ Calendar or Gmail service not available. Please check authentication."})
            
            # Validate participant email
            if not validate_email(details['participant_email']):
                session.clear()
                return jsonify({"reply": "❌ Invalid participant email address."})
            
            try:
                start_time, end_time = parse_datetime(details['event_date'], details['event_time'])
                
                # Check for conflicts first
                has_conflict = check_participant_calendar_conflicts(
                    services['calendar'],
                    details['participant_email'],
                    start_time,
                    end_time
                )
                
                if has_conflict:
                    send_conflict_notification(
                        services['gmail'],
                        details['participant_email'],
                        details['event_name'],
                        start_time,
                        end_time
                    )
                    msg = f"⚠️ Participant '{details['participant_email']}' has a scheduling conflict at the requested time. They have been notified about the conflict."
                else:
                    # Create event with meeting link and let Google Calendar send the invite
                    event_created, actual_meeting_link = create_event_with_meeting_link(
                        services['calendar'],
                        summary=details['event_name'],
                        start_time=start_time,
                        end_time=end_time,
                        participant_email=details['participant_email']
                    )
                    
                    if event_created:
                        # Track the scheduled meeting
                        track_scheduled_meeting(
                            details['participant_email'],
                            details['event_name'],
                            details['event_date'],
                            details['event_time'],
                            actual_meeting_link,
                            event_created.get('id')
                        )
                        
                        msg = f"✅ Event '{details['event_name']}' scheduled successfully with {details['participant_email']}!\n\n🔗 Meeting Link: {actual_meeting_link}\n\n📧 A calendar invitation has been sent with meeting details.\n\n📊 Meeting count updated in email dashboard: {get_scheduled_meetings_count()} total scheduled meetings."
                    else:
                        msg = f"❌ Failed to create calendar event. Please check your calendar permissions."

            except Exception as e:
                print(f"DEBUG - Error in scheduling: {e}")
                msg = f"❌ Error scheduling meeting: {str(e)}"

            session.clear()
            return jsonify({"reply": msg})

        elif intent == 'update':
            # Handle user input for the waiting_for field
            if 'waiting_for' in session:
                field = session['waiting_for']
                success, message = process_update_field_input(field, user_input, session['data'])
                
                if success:
                    session.pop('waiting_for', None)
                    session.modified = True
                else:
                    return jsonify({"reply": message})
            
            # Check for missing fields and prompt for them
            field, prompt = get_missing_update_field_prompt(session['data'])
            if field:
                session['waiting_for'] = field
                session.modified = True
                print(f"DEBUG - Missing update field: {field}, prompting user")
                return jsonify({"reply": prompt})

            # All fields are present, proceed with ENHANCED update
            details = session['data']
            print(f"DEBUG - All update fields present, proceeding with enhanced update: {details}")
            
            if not services or 'calendar' not in services or 'gmail' not in services:
                session.clear()
                return jsonify({"reply": "❌ Services not available. Please check authentication."})

            try:
                # Use the enhanced update function
                success, message = update_event_with_sync(
                    services['calendar'],
                    services['gmail'],
                    details['event_name'],
                    details['new_date'],
                    details['new_time']
                )
                
                if success:
                    # Sync session data with calendar
                    sync_session_with_calendar(services['calendar'])
                    
                    # Add meeting count info
                    meeting_count = get_scheduled_meetings_count()
                    enhanced_message = f"{message}\n\n📊 Calendar synchronized. Total scheduled meetings: {meeting_count}"
                    
                    session.clear()
                    return jsonify({"reply": enhanced_message})
                else:
                    session.clear()
                    return jsonify({"reply": f"❌ {message}"})
                        
            except Exception as e:
                print(f"DEBUG - Error in enhanced update: {e}")
                session.clear()
                return jsonify({"reply": f"❌ Error updating meeting: {str(e)}"})

        # ===== ENHANCED DELETE FLOW =====
        elif intent == 'delete':
            details = session['data']
            
            # Handle waiting for event name input
            if 'waiting_for' in session and session['waiting_for'] == 'event_name':
                event_name = user_input.strip()
                if event_name:
                    details['event_name'] = event_name
                    session['data'] = details
                    session.pop('waiting_for', None)
                    session.modified = True
                else:
                    return jsonify({"reply": "⚠️ Please enter a valid event name."})
            
            # Check if we still need event name with enhanced event listing
            if 'event_name' not in details or not details['event_name']:
                try:
                    if services and 'calendar' in services:
                        # Get recent events from calendar with better filtering
                        now = datetime.utcnow()
                        time_min = (now - timedelta(days=1)).isoformat() + 'Z'
                        time_max = (now + timedelta(days=30)).isoformat() + 'Z'
                        
                        events_result = services['calendar'].events().list(
                            calendarId='primary',
                            timeMin=time_min,
                            timeMax=time_max,
                            maxResults=20,  # Increased to show more options
                            singleEvents=True,
                            orderBy='startTime'
                        ).execute()
                        
                        events = events_result.get('items', [])
                        user_email = get_authenticated_user_email()
                        
                        # Filter deletable events more accurately
                        deletable_events = []
                        for event in events:
                            organizer = event.get('organizer', {})
                            creator = event.get('creator', {})
                            
                            # Check if user is organizer or creator
                            is_user_organizer = (
                                organizer.get('email', '').lower() == user_email.lower() if user_email else False or
                                creator.get('email', '').lower() == user_email.lower() if user_email else False
                            )
                            
                            if is_user_organizer and event.get('summary'):
                                start_time = event.get('start', {})
                                start_str = start_time.get('dateTime', start_time.get('date', ''))
                                
                                # Format date nicely
                                if start_str:
                                    if 'T' in start_str:
                                        parsed_start = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
                                        formatted_date = parsed_start.strftime('%b %d, %Y at %I:%M %p')
                                    else:
                                        formatted_date = start_str
                                else:
                                    formatted_date = 'Date unknown'
                                
                                deletable_events.append({
                                    'name': event['summary'],
                                    'formatted_date': formatted_date,
                                    'id': event['id'],
                                    'has_attendees': len(event.get('attendees', [])) > 1
                                })
                        
                        if deletable_events:
                            event_list = "📅 **Your Deletable Events:**\n\n"
                            for i, event in enumerate(deletable_events[:8]):  # Show max 8
                                attendee_note = " 👥" if event['has_attendees'] else ""
                                event_list += f"{i+1}. **{event['name']}**{attendee_note}\n   📅 {event['formatted_date']}\n\n"
                            
                            event_list += "💡 Events with 👥 have attendees who will be notified.\n"
                            event_list += "Enter the **event name** or **number** to delete:"
                            
                            session['waiting_for'] = 'event_name'
                            session['deletable_events'] = deletable_events  # Store for number selection
                            session.modified = True
                            
                            return jsonify({
                                "reply": event_list
                            })
                        else:
                            session.clear()
                            return jsonify({"reply": "📅 No deletable events found in your calendar (you must be the organizer to delete events)."})
                            
                except Exception as e:
                    print(f"Error fetching events: {e}")
                
                # Fallback
                session['waiting_for'] = 'event_name'
                session.modified = True
                return jsonify({"reply": "🗑️ What is the name of the event you want to delete?"})

            # Handle number selection for events
            if user_input.isdigit() and 'deletable_events' in session:
                try:
                    event_index = int(user_input) - 1
                    deletable_events = session.get('deletable_events', [])
                    if 0 <= event_index < len(deletable_events):
                        selected_event = deletable_events[event_index]
                        details['event_name'] = selected_event['name']
                        session['data'] = details
                        session.pop('deletable_events', None)
                        session.modified = True
                        print(f"DEBUG - Selected event by number: {selected_event['name']}")
                    else:
                        return jsonify({"reply": "⚠️ Invalid event number. Please try again."})
                except ValueError:
                    pass  # Not a number, continue with regular processing

            # Proceed with ENHANCED deletion
            if not services or 'calendar' not in services or 'gmail' not in services:
                session.clear()
                return jsonify({"reply": "❌ Services not available. Please check authentication."})

            try:
                # Use the enhanced delete function
                success, message = delete_event_with_sync(
                    services['calendar'],
                    services['gmail'],
                    details['event_name']
                )
                
                if success:
                    # Sync session data with calendar
                    sync_session_with_calendar(services['calendar'])
                    
                    # Add meeting count info
                    meeting_count = get_scheduled_meetings_count()
                    enhanced_message = f"{message}\n\n📊 Calendar synchronized. Total scheduled meetings: {meeting_count}"
                    
                    session.clear()
                    return jsonify({"reply": enhanced_message})
                else:
                    session.clear()
                    return jsonify({"reply": f"❌ {message}"})
                    
            except Exception as e:
                print(f"DEBUG - Error in enhanced delete: {e}")
                session.clear()
                return jsonify({"reply": f"❌ Error deleting event: {str(e)}"})

        return jsonify({"reply": "I didn't understand that request. Please try scheduling a meeting, updating an event, deleting an event, or checking your emails."})

    except Exception as e:
        print(f"❌ Error in chat route: {e}")
        session.clear()
        return jsonify({"reply": "❌ An error occurred. Please try again."})

# Enhanced Calendar Synchronization Functions
# Add these improvements to your existing code

@app.route('/api/meeting/<meeting_id>/cancel', methods=['POST'])
def cancel_meeting_enhanced(meeting_id):
    """Enhanced cancel meeting with proper calendar sync"""
    try:
        # Get meeting details
        all_meetings = get_all_scheduled_meetings()
        meeting = next((m for m in all_meetings if m['id'] == meeting_id), None)
        
        if not meeting:
            return jsonify({'success': False, 'error': 'Meeting not found'})
        
        if not services or 'calendar' not in services or 'gmail' not in services:
            return jsonify({'success': False, 'error': 'Services not available'})
        
        # Use enhanced delete function if we have the event name
        if meeting.get('event_name'):
            success, message = delete_event_with_sync(
                services['calendar'],
                services['gmail'],
                meeting['event_name']
            )
            
            if success:
                # Sync session data
                sync_session_with_calendar(services['calendar'])
                
                return jsonify({
                    'success': True,
                    'message': message,
                    'total_meetings': get_scheduled_meetings_count()
                })
            else:
                return jsonify({'success': False, 'error': message})
        else:
            # Fallback to direct calendar deletion
            try:
                if meeting.get('calendar_event_id'):
                    # Send cancellation email first
                    participant_emails = meeting.get('participant_emails', [])
                    if not participant_emails and meeting.get('participant_email'):
                        participant_emails = [meeting['participant_email']]
                    
                    for email in participant_emails:
                        send_meeting_cancellation_email(
                            services['gmail'],
                            email,
                            meeting.get('event_name', 'Meeting'),
                            "Meeting cancelled via AI Calendar System"
                        )
                    
                    # Delete from calendar
                    services['calendar'].events().delete(
                        calendarId='primary',
                        eventId=meeting['calendar_event_id'],
                        sendUpdates='all'
                    ).execute()
                    
                    print(f"✅ Deleted calendar event: {meeting['calendar_event_id']}")
                    
                    # Sync session data
                    sync_session_with_calendar(services['calendar'])
                    
                    return jsonify({
                        'success': True,
                        'message': f'Meeting "{meeting.get("event_name", "Unknown")}" cancelled successfully',
                        'total_meetings': get_scheduled_meetings_count()
                    })
                else:
                    return jsonify({'success': False, 'error': 'No calendar event ID found'})
                    
            except Exception as calendar_error:
                print(f"❌ Error in direct calendar deletion: {calendar_error}")
                return jsonify({'success': False, 'error': f'Failed to cancel meeting: {str(calendar_error)}'})
        
    except Exception as error:
        print(f"❌ Error in cancel_meeting_enhanced: {error}")
        return jsonify({'success': False, 'error': str(error)})

# Additional utility functions for better calendar synchronization

def refresh_calendar_data():
    """Refresh all calendar-related data and sync with session"""
    try:
        if not services or 'calendar' not in services:
            return False, "Calendar service not available"
        
        print("🔄 Refreshing calendar data...")
        
        # Get fresh calendar meetings
        calendar_meetings = get_scheduled_meetings_from_calendar()
        
        # Sync session data
        sync_session_with_calendar(services['calendar'])
        
        # Update any cached data
        session['last_calendar_refresh'] = datetime.now().isoformat()
        session.modified = True
        
        print(f"✅ Calendar data refreshed. Found {len(calendar_meetings)} meetings")
        return True, f"Calendar refreshed successfully. {len(calendar_meetings)} meetings found"
        
    except Exception as error:
        print(f"❌ Error refreshing calendar data: {error}")
        return False, f"Error refreshing calendar: {str(error)}"

@app.route('/api/refresh_calendar_enhanced', methods=['POST'])
def refresh_calendar_enhanced():
    """Enhanced calendar refresh endpoint"""
    try:
        success, message = refresh_calendar_data()
        
        if success:
            return jsonify({
                'success': True,
                'message': message,
                'scheduled_meetings': get_all_scheduled_meetings(),
                'total_meetings': get_scheduled_meetings_count(),
                'last_refresh': session.get('last_calendar_refresh')
            })
        else:
            return jsonify({
                'success': False,
                'error': message
            }), 500
            
    except Exception as error:
        return jsonify({
            'success': False,
            'error': str(error)
        }), 500

def validate_calendar_sync():
    """Validate that calendar operations are properly synchronized"""
    try:
        if not services or 'calendar' not in services:
            return False, "Calendar service not available"
        
        # Get session meetings
        session_meetings = session.get('scheduled_meetings', [])
        
        # Get calendar meetings
        calendar_meetings = get_scheduled_meetings_from_calendar()
        
        # Check for mismatches
        mismatches = []
        
        for session_meeting in session_meetings:
            calendar_event_id = session_meeting.get('calendar_event_id')
            if calendar_event_id:
                # Check if this event exists in calendar
                calendar_match = next(
                    (cm for cm in calendar_meetings if cm.get('calendar_event_id') == calendar_event_id),
                    None
                )
                if not calendar_match:
                    mismatches.append(f"Session meeting '{session_meeting.get('event_name')}' not found in calendar")
        
        if mismatches:
            print(f"⚠️ Calendar sync issues detected: {len(mismatches)} mismatches")
            for mismatch in mismatches:
                print(f"  - {mismatch}")
            return False, f"{len(mismatches)} sync issues detected"
        else:
            print("✅ Calendar sync validation passed")
            return True, "Calendar sync validated successfully"
            
    except Exception as error:
        print(f"❌ Error validating calendar sync: {error}")
        return False, f"Validation error: {str(error)}"

@app.route('/api/validate_calendar_sync', methods=['GET'])
def validate_calendar_sync_endpoint():
    """Endpoint to validate calendar synchronization"""
    try:
        success, message = validate_calendar_sync()
        
        return jsonify({
            'success': success,
            'message': message,
            'session_meetings_count': len(session.get('scheduled_meetings', [])),
            'calendar_meetings_count': len(get_scheduled_meetings_from_calendar()),
            'total_meetings_count': get_scheduled_meetings_count()
        })
        
    except Exception as error:
        return jsonify({
            'success': False,
            'error': str(error)
        }), 500

# Enhanced error recovery for calendar operations
def recover_from_calendar_error(operation_type, event_data, error):
    """Attempt to recover from calendar operation errors"""
    try:
        print(f"🔧 Attempting to recover from {operation_type} error: {error}")
        
        if "401" in str(error):
            # Authentication error
            return False, "Authentication expired. Please re-authenticate with Google Calendar"
            
        elif "403" in str(error):
            # Permission error
            return False, "Permission denied. Check your Google Calendar permissions"
            
        elif "404" in str(error):
            if operation_type == 'delete':
                # Event already deleted
                print("ℹ️ Event already deleted, cleaning up session data")
                sync_session_with_calendar(services['calendar'])
                return True, "Event was already deleted"
            elif operation_type == 'update':
                # Event doesn't exist anymore
                return False, "Event no longer exists and cannot be updated"
                
        elif "429" in str(error):
            # Rate limit
            return False, "Rate limit exceeded. Please try again in a few moments"
            
        elif "503" in str(error) or "502" in str(error):
            # Service temporarily unavailable
            return False, "Google Calendar service temporarily unavailable. Please try again later"
            
        else:
            # Unknown error
            print(f"⚠️ Unknown calendar error: {error}")
            return False, f"Calendar operation failed: {str(error)}"
            
    except Exception as recovery_error:
        print(f"❌ Error in recovery attempt: {recovery_error}")
        return False, f"Recovery failed: {str(recovery_error)}"

# Integration with existing functions
def delete_event_with_recovery(calendar_service, gmail_service, event_name):
    """Delete event with error recovery"""
    try:
        success, message = delete_event_with_sync(calendar_service, gmail_service, event_name)
        return success, message
        
    except Exception as error:
        print(f"❌ Delete operation failed, attempting recovery: {error}")
        return recover_from_calendar_error('delete', {'event_name': event_name}, error)

def update_event_with_recovery(calendar_service, gmail_service, event_name, new_date, new_time):
    """Update event with error recovery"""
    try:
        success, message = update_event_with_sync(calendar_service, gmail_service, event_name, new_date, new_time)
        return success, message
        
    except Exception as error:
        print(f"❌ Update operation failed, attempting recovery: {error}")
        return recover_from_calendar_error('update', {
            'event_name': event_name,
            'new_date': new_date,
            'new_time': new_time
        }, error)

def delete_event_with_sync(calendar_service, gmail_service, event_name):
    """Enhanced delete function with proper calendar synchronization"""
    try:
        print(f"🗑️ Starting deletion process for: {event_name}")
        
        # Find the event by name
        event = get_event_by_name(calendar_service, event_name)
        
        if not event:
            # Try fuzzy search for similar events
            suggestions = suggest_similar_events(calendar_service, event_name)
            if suggestions:
                print(f"⚠️ Event '{event_name}' not found. Found similar events: {[s['summary'] for s in suggestions]}")
                return False, f"Event '{event_name}' not found. Similar events found: {', '.join([s['summary'] for s in suggestions[:3]])}"
            else:
                print(f"❌ Event '{event_name}' not found")
                return False, f"Event '{event_name}' not found"
        
        # Store event details for notifications before deletion
        event_id = event['id']
        event_summary = event.get('summary', event_name)
        attendees = event.get('attendees', [])
        event_start = event.get('start', {})
        event_end = event.get('end', {})
        
        print(f"📋 Found event: {event_summary} (ID: {event_id})")
        print(f"👥 Attendees: {len(attendees)}")
        
        # Send cancellation emails to attendees BEFORE deleting
        user_email = get_authenticated_user_email()
        notification_sent = False
        
        if attendees and user_email:
            for attendee in attendees:
                attendee_email = attendee.get('email')
                if attendee_email and attendee_email.lower() != user_email.lower():
                    print(f"📧 Sending cancellation email to: {attendee_email}")
                    email_sent = send_meeting_cancellation_email(
                        gmail_service,
                        attendee_email,
                        event_summary,
                        "Meeting cancelled by organizer"
                    )
                    if email_sent:
                        notification_sent = True
                        print(f"✅ Cancellation email sent to {attendee_email}")
                    else:
                        print(f"⚠️ Failed to send cancellation email to {attendee_email}")
        
        # Delete the event from calendar with proper error handling
        try:
            calendar_service.events().delete(
                calendarId='primary',
                eventId=event_id,
                sendUpdates='all'  # This ensures attendees get calendar cancellation notices
            ).execute()
            
            print(f"✅ Event '{event_summary}' deleted successfully from calendar")
            
            # Verify deletion by trying to fetch the event
            try:
                deleted_check = calendar_service.events().get(
                    calendarId='primary',
                    eventId=event_id
                ).execute()
                # If we can still get it, it might not be fully deleted
                print(f"⚠️ Event still exists after deletion attempt: Status = {deleted_check.get('status', 'unknown')}")
                if deleted_check.get('status') != 'cancelled':
                    return False, "Event deletion may not have completed properly"
            except Exception:
                # This is expected - event should not be found after deletion
                print(f"✅ Confirmed: Event no longer exists in calendar")
            
            # Clean up session data
            if 'scheduled_meetings' in session:
                session['scheduled_meetings'] = [
                    m for m in session['scheduled_meetings'] 
                    if m.get('calendar_event_id') != event_id
                ]
                session.modified = True
                print("🧹 Session data cleaned up")
            
            success_message = f"Event '{event_summary}' deleted successfully"
            if notification_sent:
                success_message += " and cancellation notifications sent to attendees"
            
            return True, success_message
            
        except Exception as delete_error:
            error_msg = str(delete_error)
            print(f"❌ Error deleting event: {error_msg}")
            
            # Handle specific Google Calendar API errors
            if "404" in error_msg:
                return False, f"Event '{event_name}' was already deleted or not found"
            elif "403" in error_msg:
                return False, f"Permission denied. You may not have rights to delete this event"
            elif "401" in error_msg:
                return False, "Authentication error. Please re-authenticate with Google Calendar"
            else:
                return False, f"Failed to delete event: {error_msg}"
                
    except Exception as error:
        print(f"❌ Unexpected error in delete_event_with_sync: {error}")
        return False, f"Unexpected error during deletion: {str(error)}"

def update_event_with_sync(calendar_service, gmail_service, event_name, new_date, new_time):
    """Enhanced update function with proper calendar synchronization"""
    try:
        print(f"🔄 Starting update process for: {event_name}")
        
        # Find the event
        event = get_event_by_name(calendar_service, event_name)
        
        if not event:
            suggestions = suggest_similar_events(calendar_service, event_name)
            if suggestions:
                suggestion_text = ', '.join([s['summary'] for s in suggestions[:3]])
                return False, f"Event '{event_name}' not found. Did you mean: {suggestion_text}?"
            else:
                return False, f"Event '{event_name}' not found"
        
        # Parse new datetime
        try:
            new_start_time, new_end_time = parse_datetime(new_date, new_time)
        except Exception as parse_error:
            return False, f"Invalid date/time format: {str(parse_error)}"
        
        # Store original details
        original_start = event.get('start', {})
        original_end = event.get('end', {})
        attendees = event.get('attendees', [])
        event_id = event['id']
        event_summary = event.get('summary', event_name)
        
        print(f"📋 Found event: {event_summary} (ID: {event_id})")
        print(f"⏰ Original time: {original_start.get('dateTime', 'unknown')} - {original_end.get('dateTime', 'unknown')}")
        print(f"🔄 New time: {new_start_time.isoformat()} - {new_end_time.isoformat()}")
        
        # Check for conflicts with attendees
        user_email = get_authenticated_user_email()
        conflict_detected = False
        
        if attendees and user_email:
            for attendee in attendees:
                attendee_email = attendee.get('email')
                if attendee_email and attendee_email.lower() != user_email.lower():
                    has_conflict = check_participant_calendar_conflicts(
                        calendar_service,
                        attendee_email,
                        new_start_time,
                        new_end_time
                    )
                    if has_conflict:
                        print(f"⚠️ Conflict detected for {attendee_email}")
                        send_conflict_notification(
                            gmail_service,
                            attendee_email,
                            event_summary,
                            new_start_time,
                            new_end_time
                        )
                        conflict_detected = True
        
        if conflict_detected:
            return False, "Scheduling conflicts detected. Participants have been notified."
        
        # Send reschedule notification emails BEFORE updating
        notification_sent = False
        if attendees and user_email:
            for attendee in attendees:
                attendee_email = attendee.get('email')
                if attendee_email and attendee_email.lower() != user_email.lower():
                    print(f"📧 Sending reschedule notification to: {attendee_email}")
                    
                    # Format dates for email
                    formatted_date = new_start_time.strftime('%A, %B %d, %Y')
                    formatted_time = f"{new_start_time.strftime('%I:%M %p')} - {new_end_time.strftime('%I:%M %p')}"
                    
                    email_sent = send_enhanced_email(
                        gmail_service,
                        attendee_email,
                        f"📅 Meeting Rescheduled: {event_summary}",
                        f"""Hi,

The meeting '{event_summary}' has been rescheduled.

New Details:
Date: {formatted_date}
Time: {formatted_time}

You will receive an updated calendar invitation shortly.

Best regards""",
                        f"""<html><body>
                        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
                            <div style="background: linear-gradient(135deg, #3b82f6 0%, #1d4ed8 100%); color: white; padding: 30px; border-radius: 10px; text-align: center;">
                                <h1 style="margin: 0; font-size: 28px;">📅 Meeting Rescheduled</h1>
                            </div>
                            <div style="background-color: #f0f9ff; padding: 25px; border-radius: 8px; margin: 25px 0; border-left: 5px solid #3b82f6;">
                                <h2 style="color: #1d4ed8; margin: 10px 0;">{event_summary}</h2>
                                <div style="background-color: #dbeafe; padding: 15px; border-radius: 6px; margin: 15px 0;">
                                    <p style="margin: 0; color: #1e40af;"><strong>New Date:</strong> {formatted_date}</p>
                                    <p style="margin: 0; color: #1e40af;"><strong>New Time:</strong> {formatted_time}</p>
                                </div>
                                <p style="color: #1e40af;">You will receive an updated calendar invitation shortly.</p>
                            </div>
                        </div>
                        </body></html>"""
                    )
                    
                    if email_sent:
                        notification_sent = True
                        print(f"✅ Reschedule notification sent to {attendee_email}")
                    else:
                        print(f"⚠️ Failed to send reschedule notification to {attendee_email}")
        
        # Update the calendar event
        try:
            # Prepare the updated event data
            updated_event = event.copy()  # Start with the existing event
            
            # Update the time fields
            updated_event['start'] = {
                'dateTime': new_start_time.isoformat(),
                'timeZone': original_start.get('timeZone', 'UTC')
            }
            updated_event['end'] = {
                'dateTime': new_end_time.isoformat(),
                'timeZone': original_end.get('timeZone', 'UTC')
            }
            
            # Add/update description with update note
            original_description = updated_event.get('description', '')
            update_note = f"\n\n[Updated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} via AI Calendar Assistant]"
            updated_event['description'] = original_description + update_note
            
            # Execute the update
            result = calendar_service.events().update(
                calendarId='primary',
                eventId=event_id,
                body=updated_event,
                sendUpdates='all'  # Send updates to all attendees
            ).execute()
            
            print(f"✅ Event updated successfully in calendar")
            print(f"🔗 Updated event link: {result.get('htmlLink', 'N/A')}")
            
            # Update session data if exists
            if 'scheduled_meetings' in session:
                for meeting in session['scheduled_meetings']:
                    if meeting.get('calendar_event_id') == event_id:
                        meeting['event_date'] = new_date
                        meeting['event_time'] = new_time
                        break
                session.modified = True
                print("🧹 Session data updated")
            
            # Verify the update by fetching the updated event
            try:
                verification = calendar_service.events().get(
                    calendarId='primary',
                    eventId=event_id
                ).execute()
                
                updated_start = verification.get('start', {}).get('dateTime', '')
                if new_start_time.isoformat() in updated_start:
                    print("✅ Update verified successfully")
                else:
                    print("⚠️ Update verification inconclusive")
                    
            except Exception as verify_error:
                print(f"⚠️ Could not verify update: {verify_error}")
            
            success_message = f"Event '{event_summary}' updated successfully to {new_date} at {new_time}"
            if notification_sent:
                success_message += " and participants have been notified"
            
            return True, success_message
            
        except Exception as update_error:
            error_msg = str(update_error)
            print(f"❌ Error updating event: {error_msg}")
            
            # Handle specific errors
            if "404" in error_msg:
                return False, f"Event '{event_name}' no longer exists"
            elif "403" in error_msg:
                return False, "Permission denied. You may not have rights to update this event"
            elif "401" in error_msg:
                return False, "Authentication error. Please re-authenticate with Google Calendar"
            else:
                return False, f"Failed to update event: {error_msg}"
                
    except Exception as error:
        print(f"❌ Unexpected error in update_event_with_sync: {error}")
        return False, f"Unexpected error during update: {str(error)}"

def verify_calendar_operation(calendar_service, event_id, operation_type):
    """Verify that calendar operations completed successfully"""
    try:
        if operation_type == 'delete':
            # For delete, we expect to NOT find the event
            try:
                event = calendar_service.events().get(
                    calendarId='primary',
                    eventId=event_id
                ).execute()
                
                # If we can still get it, check if it's cancelled
                if event.get('status') == 'cancelled':
                    return True, "Event successfully cancelled"
                else:
                    return False, "Event still active after deletion attempt"
                    
            except Exception:
                # Event not found is expected for successful deletion
                return True, "Event successfully deleted"
                
        elif operation_type == 'update':
            # For update, verify the event still exists and has correct time
            try:
                event = calendar_service.events().get(
                    calendarId='primary',
                    eventId=event_id
                ).execute()
                return True, "Event successfully updated"
            except Exception:
                return False, "Event not found after update"
                
    except Exception as error:
        return False, f"Verification failed: {str(error)}"

def sync_session_with_calendar(calendar_service):
    """Synchronize session data with actual calendar state"""
    try:
        print("🔄 Syncing session data with calendar...")
        
        # Get current calendar events
        calendar_meetings = get_scheduled_meetings_from_calendar()
        
        # Update session with fresh calendar data
        if 'scheduled_meetings' in session:
            # Remove any session meetings that no longer exist in calendar
            session_meetings = session['scheduled_meetings']
            valid_session_meetings = []
            
            for session_meeting in session_meetings:
                calendar_event_id = session_meeting.get('calendar_event_id')
                if calendar_event_id:
                    # Check if this event still exists in calendar
                    still_exists = any(
                        cal_meeting.get('calendar_event_id') == calendar_event_id 
                        for cal_meeting in calendar_meetings
                    )
                    if still_exists:
                        valid_session_meetings.append(session_meeting)
                    else:
                        print(f"🧹 Removing stale session meeting: {session_meeting.get('event_name', 'Unknown')}")
                else:
                    # Keep meetings without calendar IDs (might be recent additions)
                    valid_session_meetings.append(session_meeting)
            
            session['scheduled_meetings'] = valid_session_meetings
            session.modified = True
            
        print(f"✅ Session sync completed. Found {len(calendar_meetings)} calendar events")
        return True
        
    except Exception as error:
        print(f"⚠️ Error syncing session with calendar: {error}")
        return False

# Updated route handlers using the new functions

@app.route('/api/delete_event_enhanced', methods=['POST'])
def delete_event_enhanced():
    """Enhanced delete event endpoint"""
    try:
        data = request.json
        event_name = data.get('event_name', '').strip()
        
        if not event_name:
            return jsonify({
                'success': False,
                'error': 'Event name is required'
            }), 400
        
        if not services or 'calendar' not in services or 'gmail' not in services:
            return jsonify({
                'success': False,
                'error': 'Services not available'
            }), 500
        
        # Use the enhanced delete function
        success, message = delete_event_with_sync(
            services['calendar'],
            services['gmail'],
            event_name
        )
        
        if success:
            # Sync session data
            sync_session_with_calendar(services['calendar'])
            
            return jsonify({
                'success': True,
                'message': message,
                'total_meetings': get_scheduled_meetings_count()
            })
        else:
            return jsonify({
                'success': False,
                'error': message
            }), 400
            
    except Exception as error:
        return jsonify({
            'success': False,
            'error': str(error)
        }), 500

@app.route('/api/update_event_enhanced', methods=['POST'])
def update_event_enhanced():
    """Enhanced update event endpoint"""
    try:
        data = request.json
        event_name = data.get('event_name', '').strip()
        new_date = data.get('new_date', '').strip()
        new_time = data.get('new_time', '').strip()
        
        # Validation
        if not all([event_name, new_date, new_time]):
            return jsonify({
                'success': False,
                'error': 'Event name, new date, and new time are required'
            }), 400
        
        if not services or 'calendar' not in services or 'gmail' not in services:
            return jsonify({
                'success': False,
                'error': 'Services not available'
            }), 500
        
        # Use the enhanced update function
        success, message = update_event_with_sync(
            services['calendar'],
            services['gmail'],
            event_name,
            new_date,
            new_time
        )
        
        if success:
            # Sync session data
            sync_session_with_calendar(services['calendar'])
            
            return jsonify({
                'success': True,
                'message': message,
                'total_meetings': get_scheduled_meetings_count()
            })
        else:
            return jsonify({
                'success': False,
                'error': message
            }), 400
            
    except Exception as error:
        return jsonify({
            'success': False,
            'error': str(error)
        }), 500

@app.route('/api/search_events', methods=['GET'])
def search_events_api():
    """Search for events by name"""
    try:
        query = request.args.get('q', '').strip()
        
        if not query:
            return jsonify({
                'success': False,
                'error': 'Query parameter required'
            }), 400
        
        if not services or 'calendar' not in services:
            return jsonify({
                'success': False,
                'error': 'Calendar service not available'
            }), 500
        
        # Search for events
        suggestions = suggest_similar_events(services['calendar'], query)
        
        return jsonify({
            'success': True,
            'suggestions': suggestions,
            'count': len(suggestions)
        })
        
    except Exception as error:
        return jsonify({
            'success': False,
            'error': str(error)
        }), 500

# Enhanced error handling function
def handle_update_error(error_type, details):
    """Handle different types of update errors with appropriate responses"""
    
    error_responses = {
        'event_not_found': {
            'message': f"❌ Event '{details.get('event_name', 'Unknown')}' not found. Please check the event name.",
            'suggestions': True
        },
        'no_attendees': {
            'message': "⚠️ This event has no attendees. Cannot send update notifications.",
            'suggestions': False
        },
        'permission_denied': {
            'message': "🔒 You don't have permission to update this event.",
            'suggestions': False
        },
        'conflict_detected': {
            'message': f"⚠️ Schedule conflict detected. Notifications sent to affected participants.",
            'suggestions': False
        },
        'service_unavailable': {
            'message': "❌ Calendar or email service unavailable. Please try again later.",
            'suggestions': False
        }
    }
    
    return error_responses.get(error_type, {
        'message': f"❌ An error occurred: {details}",
        'suggestions': False
    })

# Add validation middleware
def validate_update_request(data):
    """Validate update request data"""
    errors = []
    
    if not data.get('event_name'):
        errors.append("Event name is required")
    
    if not data.get('new_date'):
        errors.append("New date is required")
    
    if not data.get('new_time'):
        errors.append("New time is required")
    
    # Validate date format
    if data.get('new_date'):
        try:
            parsed_date = dateparser.parse(data['new_date'])
            if not parsed_date or parsed_date.date() < datetime.now().date():
                errors.append("New date must be today or in the future")
        except:
            errors.append("Invalid date format")
    
    return errors

logging.basicConfig(level=logging.INFO)
update_logger = logging.getLogger('calendar_updates')

def log_update_attempt(event_name, user_input, success, message):
    """Log update attempts for debugging and monitoring"""
    log_entry = {
        'timestamp': datetime.now().isoformat(),
        'event_name': event_name,
        'user_input': user_input,
        'success': success,
        'message': message
    }
    
    if success:
        update_logger.info(f"UPDATE_SUCCESS: {log_entry}")
    else:
        update_logger.error(f"UPDATE_FAILED: {log_entry}")

# Test endpoint for debugging
@app.route('/api/test_update', methods=['POST'])
def test_update():
    """Test endpoint for update functionality"""
    try:
        if not app.debug:
            return jsonify({'error': 'Test endpoint only available in debug mode'}), 403
        
        test_data = request.json
        
        # Run through the update extraction
        user_input = test_data.get('input', '')
        extracted = extract_update_details(user_input)
        
        # Test datetime parsing if complete
        parsed_times = None
        if extracted.get('new_date') and extracted.get('new_time'):
            try:
                start_time, end_time = parse_datetime(extracted['new_date'], extracted['new_time'])
                parsed_times = {
                    'start': start_time.isoformat(),
                    'end': end_time.isoformat()
                }
            except Exception as e:
                parsed_times = f"Error parsing: {e}"
        
        return jsonify({
            'input': user_input,
            'extracted': extracted,
            'parsed_times': parsed_times,
            'is_update_intent': is_update_intent(user_input)
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def process_meeting_requests_from_chat():
    """Process meeting requests from chat flow"""
    try:
        meeting_requests = session.get('meeting_requests', [])
        
        if not meeting_requests:
            return jsonify({'reply': 'No meeting requests found to process.'})
        
        if not services or 'gmail' not in services or 'calendar' not in services:
            return jsonify({'reply': '❌ Services not available. Please check authentication.'})
        
        processed_count = 0
        conflicts_count = 0
        errors = []
        
        for email in meeting_requests[:3]:  # Process first 3
            meeting_details = extract_meeting_details_from_email(email)
            if meeting_details and meeting_details.get('has_complete_info'):
                try:
                    if not validate_email(meeting_details['participant_email']):
                        errors.append(f"Error processing {email.get('subject', 'Unknown')}: Invalid participant email.")
                        continue
                    
                    start_time, end_time = parse_datetime(
                        meeting_details['event_date'], 
                        meeting_details['event_time']
                    )
                    
                    has_conflict = check_participant_calendar_conflicts(
                        services['calendar'],
                        meeting_details['participant_email'],
                        start_time,
                        end_time
                    )
                    
                    if has_conflict:
                        send_conflict_notification(
                            services['gmail'],
                            meeting_details['participant_email'],
                            meeting_details['event_name'],
                            start_time,
                            end_time
                        )
                        conflicts_count += 1
                    else:
                        event_created, actual_meeting_link = create_event_with_meeting_link(
                            services['calendar'],
                            summary=meeting_details['event_name'],
                            start_time=start_time,
                            end_time=end_time,
                            participant_email=meeting_details['participant_email']
                        )
                        
                        if event_created:
                            track_scheduled_meeting(
                                meeting_details['participant_email'],
                                meeting_details['event_name'],
                                meeting_details['event_date'],
                                meeting_details['event_time'],
                                actual_meeting_link,
                                event_created.get('id')
                            )
                            processed_count += 1
                        else:
                            errors.append(f"Error processing {email.get('subject', 'Unknown')}: Failed to create calendar event.")
                            
                except Exception as e:
                    errors.append(f"Error processing {email.get('subject', 'Unknown')}: {str(e)}")
            else:
                errors.append(f"Skipping {email.get('subject', 'Unknown')}: Incomplete meeting details extracted.")
        
        response_msg = f"✅ Processed {processed_count} meeting requests successfully!"
        if conflicts_count > 0:
            response_msg += f"\n⚠️ {conflicts_count} meetings had conflicts and participants were notified."
        if errors:
            response_msg += f"\n❌ There were errors processing {len(errors)} meetings."
        response_msg += "\n📅 Check your calendar for the scheduled events."
        response_msg += f"\n📊 Meeting count updated: {get_scheduled_meetings_count()} total scheduled meetings."
        
        return jsonify({
            "reply": response_msg,
            "action": "clear_session"
        })
        
    except Exception as error:
        return jsonify({"reply": f"❌ Error processing meetings: {str(error)}"})

@app.route('/api/process_meeting_requests', methods=['POST'])
def process_meeting_requests():
    """Process meeting requests automatically with enhanced email notifications"""
    try:
        meeting_requests = session.get('meeting_requests', [])
        
        if not meeting_requests:
            return jsonify({
                'success': False,
                'message': 'No meeting requests found'
            })
        
        if not services or 'gmail' not in services or 'calendar' not in services:
            return jsonify({
                'success': False,
                'error': 'Services not available'
            }), 500
        
        processed_meetings = []
        
        for email in meeting_requests:
            meeting_details = extract_meeting_details_from_email(email)
            
            if meeting_details and meeting_details.get('has_complete_info'):
                try:
                    if not validate_email(meeting_details['participant_email']):
                        processed_meetings.append({
                            'email_subject': email['subject'],
                            'status': 'error: invalid participant email'
                        })
                        continue
                    
                    start_time, end_time = parse_datetime(
                        meeting_details['event_date'], 
                        meeting_details['event_time']
                    )
                    
                    has_conflict = check_participant_calendar_conflicts(
                        services['calendar'],
                        meeting_details['participant_email'],
                        start_time,
                        end_time
                    )
                    
                    if has_conflict:
                        send_conflict_notification(
                            services['gmail'],
                            meeting_details['participant_email'],
                            meeting_details['event_name'],
                            start_time,
                            end_time
                        )
                        processed_meetings.append({
                            'email_subject': email['subject'],
                            'event_name': meeting_details['event_name'],
                            'status': 'conflict: participant has another meeting'
                        })
                    else:
                        event_created, actual_meeting_link = create_event_with_meeting_link(
                            services['calendar'],
                            summary=meeting_details['event_name'],
                            start_time=start_time,
                            end_time=end_time,
                            participant_email=meeting_details['participant_email']
                        )
                        
                        if event_created:
                            track_scheduled_meeting(
                                meeting_details['participant_email'],
                                meeting_details['event_name'],
                                meeting_details['event_date'],
                                meeting_details['event_time'],
                                actual_meeting_link,
                                event_created.get('id')
                            )
                            
                            processed_meetings.append({
                                'email_subject': email['subject'],
                                'event_name': meeting_details['event_name'],
                                'status': 'scheduled successfully',
                                'meeting_link': actual_meeting_link
                            })
                        else:
                            processed_meetings.append({
                                'email_subject': email['subject'],
                                'status': 'error: failed to create calendar event'
                            })
                            
                except Exception as e:
                    processed_meetings.append({
                        'email_subject': email['subject'],
                        'status': f'error: {str(e)}'
                    })
            else:
                processed_meetings.append({
                    'email_subject': email['subject'],
                    'status': 'incomplete_info'
                })
        
        return jsonify({
            'success': True,
            'processed_meetings': processed_meetings,
            'total_scheduled_meetings': get_scheduled_meetings_count()
        })
        
    except Exception as error:
        return jsonify({
            'success': False,
            'error': str(error)
        }), 500

@app.route('/api/email/<email_id>')
def get_email_details(email_id):
    """Get detailed view of specific email"""
    processed_emails = session.get('processed_emails', [])
    email = next((e for e in processed_emails if e['id'] == email_id), None)
    
    if email:
        return jsonify({'success': True, 'email': email})
    else:
        return jsonify({'success': False, 'error': 'Email not found'}), 404

@app.route('/api/test_email', methods=['POST'])
def test_email_sending():
    """Test email sending functionality"""
    try:
        data = request.json
        recipient = data.get('recipient')
        
        if not recipient or not validate_email(recipient):
            return jsonify({'success': False, 'error': 'Invalid email address'})
        
        if not services or 'gmail' not in services:
            return jsonify({'success': False, 'error': 'Gmail service not available'})
        
        success = send_enhanced_email(
            services['gmail'],
            recipient,
            "Test Message from AI Calendar System",
            "This is a plain text test message from the AI Calendar System.",
            "<html><body><p>This is an HTML test message from the AI Calendar System.</p></body></html>"
        )
        
        if success:
            return jsonify({'success': True, 'message': f'Test email sent successfully to {recipient}'})
        else:
            return jsonify({'success': False, 'error': 'Failed to send email'})
            
    except Exception as error:
        return jsonify({'success': False, 'error': str(error)})

@app.route('/api/clear_session', methods=['POST'])
def clear_session():
    """Clear session data"""
    session.clear()
    return jsonify({'success': True})

@app.route('/api/send_custom_email', methods=['POST'])
def send_custom_email():
    """Send custom email to participants"""
    try:
        data = request.json
        recipient = data.get('recipient')
        subject = data.get('subject', 'Message from Calendar System')
        message = data.get('message', '')
        
        if not recipient or not validate_email(recipient):
            return jsonify({'success': False, 'error': 'Invalid email address'})
        
        if not message:
            return jsonify({'success': False, 'error': 'Message content required'})
        
        # Send custom email
        success = send_enhanced_email(
            services['gmail'],
            recipient,
            subject,
            message,
            f"""
            <html>
            <body>
                <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
                    <h2 style="color: #2563eb;">{subject}</h2>
                    <div style="background-color: #f8fafc; padding: 20px; border-radius: 8px; margin: 20px 0;">
                        {message.replace('\n', '<br>')}
                    </div>
                    <div style="margin-top: 30px; padding-top: 20px; border-top: 1px solid #e5e7eb;">
                        <p style="color: #6b7280; font-size: 14px;">
                            This message was sent by the AI Calendar Management System.
                        </p>
                    </div>
                </div>
            </body>
            </html>
            """
        )
        
        if success:
            return jsonify({'success': True, 'message': f'Email sent successfully to {recipient}'})
        else:
            return jsonify({'success': False, 'error': 'Failed to send email'})
            
    except Exception as error:
        return jsonify({'success': False, 'error': str(error)})

@app.route('/api/refresh_calendar', methods=['POST'])
def refresh_calendar():
    """Refresh calendar data and sync scheduled meetings"""
    try:
        if not services or 'calendar' not in services:
            return jsonify({'success': False, 'error': 'Calendar service not available'})
        
        # Get fresh calendar data
        calendar_meetings = get_scheduled_meetings_from_calendar()
        
        # Update session with fresh calendar data
        session['calendar_meetings'] = calendar_meetings
        session.modified = True
        
        # Get combined meetings count
        total_meetings = get_scheduled_meetings_count()
        
        return jsonify({
            'success': True,
            'calendar_meetings': calendar_meetings,
            'total_meetings': total_meetings,
            'message': f'Calendar refreshed successfully. Found {len(calendar_meetings)} meetings from calendar.'
        })
        
    except Exception as error:
        return jsonify({'success': False, 'error': str(error)})

@app.route('/api/stats', methods=['GET'])
def get_system_stats():
    """Get system statistics"""
    try:
        # Get email stats
        processed_emails = session.get('processed_emails', [])
        meeting_requests = session.get('meeting_requests', [])
        
        # Get calendar stats
        scheduled_meetings = get_all_scheduled_meetings()
        
        # Calculate stats
        total_emails = len(processed_emails)
        high_priority_emails = len([e for e in processed_emails if e.get('ai_urgency') == 'high'])
        action_required_emails = len([e for e in processed_emails if e.get('action_required')])
        meeting_request_emails = len([e for e in processed_emails if e.get('is_meeting_request', False)])
        
        stats = {
            'emails': {
                'total': total_emails,
                'high_priority': high_priority_emails,
                'action_required': action_required_emails,
                'meeting_requests': meeting_request_emails,
                'last_fetch': session.get('last_email_fetch', 'Never')
            },
            'meetings': {
                'total_scheduled': len(scheduled_meetings),
                'from_calendar': len([m for m in scheduled_meetings if m.get('id', '').startswith('cal_')]),
                'from_session': len([m for m in scheduled_meetings if not m.get('id', '').startswith('cal_')]),
                'pending_requests': len(meeting_requests)
            },
            'system': {
                'ai_model_available': model is not None,
                'services_authenticated': services is not None,
                'email_batch_size': EMAIL_CONFIG['default_batch_size'],
                'max_batch_size': EMAIL_CONFIG['max_batch_size']
            }
        }
        
        return jsonify({
            'success': True,
            'stats': stats
        })
        
    except Exception as error:
        return jsonify({'success': False, 'error': str(error)})

@app.route('/api/meeting/<meeting_id>/cancel', methods=['POST'])
def cancel_meeting(meeting_id):
    """Cancel a specific meeting"""
    try:
        # Get meeting details
        all_meetings = get_all_scheduled_meetings()
        meeting = next((m for m in all_meetings if m['id'] == meeting_id), None)
        
        if not meeting:
            return jsonify({'success': False, 'error': 'Meeting not found'})
        
        if not services or 'calendar' not in services or 'gmail' not in services:
            return jsonify({'success': False, 'error': 'Services not available'})
        
        # Send cancellation email
        if meeting.get('participant_email'):
            send_meeting_cancellation_email(
                services['gmail'],
                meeting['participant_email'],
                meeting['event_name'],
                "Meeting cancelled via AI Calendar System"
            )
        
        # Delete from calendar if it has a calendar event ID
        if meeting.get('calendar_event_id'):
            try:
                services['calendar'].events().delete(
                    calendarId='primary',
                    eventId=meeting['calendar_event_id']
                ).execute()
                print(f"✅ Deleted calendar event: {meeting['calendar_event_id']}")
            except Exception as e:
                print(f"⚠️ Error deleting calendar event: {e}")
        
        # Remove from session if it exists there
        session_meetings = session.get('scheduled_meetings', [])
        session['scheduled_meetings'] = [m for m in session_meetings if m['id'] != meeting_id]
        session.modified = True
        
        return jsonify({
            'success': True,
            'message': f'Meeting "{meeting["event_name"]}" cancelled successfully'
        })
        
    except Exception as error:
        return jsonify({'success': False, 'error': str(error)})

@app.route("/handle_input", methods=["POST"])
def handle_input():
    user_input = request.json.get("user_input", "")
    field = request.json.get("field", "")
    data = session.get("data", {})

    if field == "email":
        if validate_email(user_input.strip()):
            email = user_input.strip()
            data[field] = email
            session['data'] = data
            session.pop('waiting_for', None)
            session.modified = True
            print(f"DEBUG - Email validated and stored: {email}")
            return jsonify({"reply": "✅ Email saved successfully!"})
        else:
            return jsonify({
                "reply": "⚠️ Please enter a valid email address (e.g., user@example.com)."
            })

    elif field == "event_name":
        event_name = user_input.strip()
        if event_name:
            data[field] = event_name
            session['data'] = data
            session.pop('waiting_for', None)
            session.modified = True
            print(f"DEBUG - Event name stored: {event_name}")
            return jsonify({"reply": "✅ Event name saved successfully!"})
        else:
            return jsonify({
                "reply": "⚠️ Please enter a valid event name."
            })

    return jsonify({"reply": "⚠️ Unknown field."})

# Add these routes for complete clickable functionality

@app.route('/api/emails/category/<category>')
def get_emails_by_category(category):
    """Handle clicks on email category summaries"""
    try:
        processed_emails = session.get('processed_emails', [])
        
        if category == 'total':
            emails = processed_emails
            title = "All Emails"
        elif category == 'high_priority':
            emails = [e for e in processed_emails if e.get('ai_urgency') == 'high']
            title = "High Priority Emails"
        elif category == 'action_required':
            emails = [e for e in processed_emails if e.get('action_required')]
            title = "Action Required Emails"
        elif category == 'meetings':
            emails = [e for e in processed_emails if e.get('is_meeting_request', False)]
            title = "Meeting Request Emails"
        elif category == 'scheduled':
            emails = get_all_scheduled_meetings()
            title = "Scheduled Meetings"
        else:
            return jsonify({"error": "Invalid category"}), 400
        
        return jsonify({
            "success": True,
            "title": title,
            "emails": emails,
            "count": len(emails)
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/meeting/<meeting_id>')
def get_meeting_details(meeting_id):
    """Handle clicks on individual meeting requests"""
    try:
        meeting_requests = session.get('meeting_requests', [])
        meeting = next((m for m in meeting_requests if m.get('id') == meeting_id), None)
        
        if not meeting:
            return jsonify({"error": "Meeting not found"}), 404
        
        return jsonify({
            "success": True,
            "meeting": meeting,
            "actions": [
                {"label": "Accept", "action": "accept_meeting", "class": "btn-success"},
                {"label": "Decline", "action": "decline_meeting", "class": "btn-danger"},
                {"label": "Tentative", "action": "tentative_meeting", "class": "btn-warning"},
                {"label": "View Full Email", "action": "view_email", "class": "btn-info"}
            ]
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/action/<action>')
def handle_action(action):
    """Handle clicks on action items"""
    try:
        if action == 'show_dashboard':
            return jsonify({
                "success": True,
                "redirect": "/emails",
                "message": "Redirecting to Email Dashboard..."
            })
        elif action == 'process_meetings':
            return process_meeting_requests_from_chat()
        else:
            return jsonify({"error": "Invalid action"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    print("🚀 Starting Enhanced Email & Calendar Management App...")
    print(f"📧 Enhanced email processing enabled (Default batch: {EMAIL_CONFIG['default_batch_size']}, Max: {EMAIL_CONFIG['max_batch_size']})")
    print("🗓️ Calendar management enabled")
    print("🤖 AI-powered assistant ready")
    print("⚠️ Conflict detection enabled")
    print("✉️ Professional email templates active")
    print("🔗 Google Meet integration enabled")
    print("📊 Real-time meeting tracking from calendar")
    print("🔄 Enhanced session and calendar synchronization")
    print("✅ UPDATE FUNCTIONALITY FIXED - Proper field mapping and flow")
    print("🔧 Enhanced error handling and validation")

    # Run your FastAPI/Flask app depending on framework
    app.run(debug=True, host='0.0.0.0', port=5000)