import os
import re
import time
import json
import pickle
import dateparser
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import base64
import pytz 
from flask import Flask, request, jsonify, session

import google.generativeai as genai
from dotenv import load_dotenv

# Import the credentials manager
from credentials import IntegratedEmailCalendarManager, authenticate_services

# Load environment variables
load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'your-secret-key-here')

# Initialize Gemini AI chat
model = genai.GenerativeModel('gemini-pro')
chat = model.start_chat()

# Global services variable
services = None

def correct_schedule_spelling(text):
    """Correct common spelling mistakes in scheduling text"""
    corrections = {
        'schdule': 'schedule',
        'shedule': 'schedule',
        'schedual': 'schedule',
        'meting': 'meeting',
        'meating': 'meeting',
        'tommorow': 'tomorrow',
        'tomorow': 'tomorrow'
    }
    
    for wrong, correct in corrections.items():
        text = re.sub(r'\b' + wrong + r'\b', correct, text, flags=re.IGNORECASE)
    
    return text

def is_schedule_intent(message):
    """Check if message contains scheduling intent"""
    schedule_keywords = ['schedule', 'meet', 'meeting', 'appointment', 'book', 'plan']
    return any(keyword in message.lower() for keyword in schedule_keywords)

def is_update_intent(message):
    """Check if message contains update intent"""
    update_keywords = [
        'update', 'change', 'reschedule', 'modify', 'move', 
        'shift', 'postpone', 'advance', 'edit', 'alter'
    ]
    return any(keyword in message.lower() for keyword in update_keywords)

def is_delete_intent(message):
    """Check if message contains delete intent"""
    delete_keywords = ['delete', 'cancel', 'remove']
    return any(keyword in message.lower() for keyword in delete_keywords)

def get_authenticated_user_email():
    """Get the authenticated user's email address"""
    try:
        if services and 'gmail' in services:
            profile = services['gmail'].users().getProfile(userId='me').execute()
            return profile.get('emailAddress', 'me')
        return 'me'
    except Exception as e:
        print(f"Error getting user email: {e}")
        return 'me'

def extract_event_details(text):
    """
    Extracts event details from text using regular expressions.
    This is a simplified version for demonstration.
    """
    details = {}
    
    # Extract email
    email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', text)
    if email_match:
        details['participant_email'] = email_match.group(0)

    # Extract event name (a very simple heuristic)
    name_match = re.search(r'meeting with (.+) to', text, re.IGNORECASE) or \
                 re.search(r'meet with (.+) at', text, re.IGNORECASE) or \
                 re.search(r'schedule a (.+?) meeting', text, re.IGNORECASE)
    if name_match:
        details['event_name'] = name_match.group(1).strip()
    
    # Extract date and time
    if 'today' in text.lower():
        details['event_date'] = datetime.now().strftime('%Y-%m-%d')
    elif 'tomorrow' in text.lower():
        details['event_date'] = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')

    time_match = re.search(r'(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s*(?:to|-)?\s*(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)?', text, re.IGNORECASE)
    if time_match:
        details['event_time'] = time_match.group(0).strip()
    
    return details

def send_enhanced_email(gmail_service, to_email, subject, body, html_body=None):
    """Send enhanced email with optional HTML formatting"""
    try:
        if html_body:
            # Create multipart message for HTML email
            message = MIMEMultipart('alternative')
            message['to'] = to_email
            message['subject'] = subject
            message['from'] = 'me'
            
            # Add plain text part
            text_part = MIMEText(body, 'plain')
            message.attach(text_part)
            
            # Add HTML part
            html_part = MIMEText(html_body, 'html')
            message.attach(html_part)
        else:
            # Create simple text message
            message = MIMEText(body)
            message['to'] = to_email
            message['subject'] = subject
            message['from'] = 'me'
        
        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
        
        result = gmail_service.users().messages().send(
            userId='me',
            body={'raw': raw_message}
        ).execute()
        
        print(f"✅ Email sent successfully to {to_email}")
        return True
        
    except Exception as e:
        print(f"❌ Error sending enhanced email to {to_email}: {e}")
        return False
    
def send_update_confirmation_email(gmail_service, participant_email, event_name, old_datetime, new_datetime):
    """Send email confirmation about meeting update"""
    try:
        subject = f"📅 Meeting Updated: {event_name}"
        
        # Format datetime for display
        old_date = old_datetime.strftime('%A, %B %d, %Y')
        old_time = f"{old_datetime.strftime('%I:%M %p')}"
        
        new_date = new_datetime.strftime('%A, %B %d, %Y') 
        new_time = f"{new_datetime.strftime('%I:%M %p')}"
        
        # Plain text body
        plain_body = f"""
Hi,

The meeting "{event_name}" has been updated with new date and time:

PREVIOUS SCHEDULE:
Date: {old_date}
Time: {old_time} IST

NEW SCHEDULE:
Date: {new_date} 
Time: {new_time} IST

The updated meeting has been added to your calendar. Please check your calendar application for the updated details.

If you have any questions or conflicts with the new time, please reply to this email.

Best regards,
AI Calendar Assistant
        """
        
        # HTML body
        html_body = f"""
        <html>
        <head></head>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 30px; border-radius: 10px; text-align: center;">
                    <h1 style="margin: 0; font-size: 28px;">📅 Meeting Updated</h1>
                    <p style="margin: 10px 0 0 0; font-size: 16px; opacity: 0.9;">Your meeting schedule has been changed</p>
                </div>
                
                <div style="background-color: #f8f9fa; padding: 25px; border-radius: 8px; margin: 25px 0;">
                    <h2 style="color: #495057; margin: 0 0 20px 0; font-size: 20px;">{event_name}</h2>
                    
                    <div style="display: flex; justify-content: space-between; margin-bottom: 20px;">
                        <div style="flex: 1; margin-right: 20px;">
                            <h3 style="color: #dc3545; margin: 0 0 10px 0; font-size: 16px;">❌ Previous Schedule</h3>
                            <div style="background-color: #f8d7da; padding: 15px; border-radius: 6px; border-left: 4px solid #dc3545;">
                                <p style="margin: 0; color: #721c24;"><strong>Date:</strong> {old_date}</p>
                                <p style="margin: 5px 0 0 0; color: #721c24;"><strong>Time:</strong> {old_time} IST</p>
                            </div>
                        </div>
                        
                        <div style="flex: 1;">
                            <h3 style="color: #28a745; margin: 0 0 10px 0; font-size: 16px;">✅ New Schedule</h3>
                            <div style="background-color: #d4edda; padding: 15px; border-radius: 6px; border-left: 4px solid #28a745;">
                                <p style="margin: 0; color: #155724;"><strong>Date:</strong> {new_date}</p>
                                <p style="margin: 5px 0 0 0; color: #155724;"><strong>Time:</strong> {new_time} IST</p>
                            </div>
                        </div>
                    </div>
                </div>
                
                <div style="background-color: #e3f2fd; padding: 20px; border-radius: 8px; border-left: 5px solid #2196f3;">
                    <p style="margin: 0; color: #0d47a1;">
                        <strong>📝 Action Required:</strong> The updated meeting has been added to your calendar. 
                        Please check your calendar application for the updated details.
                    </p>
                </div>
                
                <div style="background-color: #fff3cd; padding: 15px; border-radius: 8px; margin-top: 20px; border-left: 5px solid #ffc107;">
                    <p style="margin: 0; color: #856404;">
                        <strong>⚠️ Questions or Conflicts?</strong> If you have any questions or the new time doesn't work for you, 
                        please reply to this email as soon as possible.
                    </p>
                </div>
                
                <div style="margin-top: 40px; padding-top: 25px; border-top: 2px solid #e9ecef; text-align: center;">
                    <p style="color: #6c757d; font-size: 14px; margin: 0;">
                        This message was sent by the AI Calendar Management System.<br>
                        Thank you for using our service!
                    </p>
                </div>
            </div>
        </body>
        </html>
        """
        
        return send_enhanced_email(gmail_service, participant_email, subject, plain_body, html_body)
        
    except Exception as e:
        print(f"❌ Error sending update confirmation email: {e}")
        return False
    
def update_event_with_notification(calendar_service, gmail_service, event, new_start_time, new_end_time):
    """Update existing event with new time and send notification"""
    try:
        # Store old datetime for email
        old_start_time = datetime.fromisoformat(
            event['start'].get('dateTime', event['start'].get('date')).replace('Z', '+00:00')
        )
        
        # Update event details
        event['start']['dateTime'] = new_start_time.isoformat()
        event['end']['dateTime'] = new_end_time.isoformat()
        
        # Update the event in calendar
        updated_event = calendar_service.events().update(
            calendarId='primary',
            eventId=event['id'],
            body=event,
            sendUpdates='all'  # This sends calendar updates to attendees
        ).execute()
        
        # Send custom notification email to attendees
        if 'attendees' in event and event['attendees']:
            for attendee in event['attendees']:
                attendee_email = attendee.get('email')
                if attendee_email:
                    send_update_confirmation_email(
                        gmail_service,
                        attendee_email,
                        event.get('summary', 'Meeting'),
                        old_start_time,
                        new_start_time
                    )
        
        print(f"✅ Event '{event.get('summary', 'Meeting')}' updated successfully")
        return updated_event
        
    except Exception as error:
        print(f"❌ Error updating event: {error}")
        return None

def check_participant_calendar_conflicts(calendar_service, participant_email, start_time, end_time):
    """Check if participant has calendar conflicts at the given time"""
    try:
        # Convert to datetime objects if needed
        if isinstance(start_time, str):
            start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
        else:
            start_dt = start_time
            
        if isinstance(end_time, str):
            end_dt = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
        else:
            end_dt = end_time
        
        # Get events for the time period
        events_result = calendar_service.events().list(
            calendarId='primary',
            timeMin=start_dt.isoformat(),
            timeMax=end_dt.isoformat(),
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        
        # Check for overlapping events with this participant
        for event in events:
            if 'attendees' in event:
                attendee_emails = [a.get('email', '').lower() for a in event['attendees']]
                if participant_email.lower() in attendee_emails:
                    return True  # Conflict found
        
        return False  # No conflicts
        
    except Exception as e:
        print(f"⚠️ Error checking calendar conflicts: {e}")
        return False

def send_conflict_notification(gmail_service, participant_email, event_name, start_time, end_time):
    """Send notification about calendar conflict"""
    try:
        if isinstance(start_time, str):
            start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
        else:
            start_dt = start_time
            
        formatted_start = start_dt.strftime('%A, %B %d, %Y at %I:%M %p')
        
        subject = f"⚠️ Schedule Conflict Detected: {event_name}"
        
        plain_body = f"""
Hi,

We detected a potential scheduling conflict when trying to update the meeting "{event_name}".

Proposed New Time: {formatted_start} IST

You appear to have another commitment during this time. Please suggest alternative times that work better for you.

We apologize for any inconvenience and will work to find a suitable time for everyone.

Best regards,
AI Calendar Assistant
        """
        
        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                <div style="background-color: #fff3cd; padding: 20px; border-radius: 8px; border-left: 5px solid #ffc107;">
                    <h2 style="color: #856404; margin: 0 0 15px 0;">⚠️ Schedule Conflict Detected</h2>
                    <p style="color: #856404; margin: 0;">We found a potential conflict with your calendar.</p>
                </div>
                
                <div style="padding: 20px 0;">
                    <p><strong>Meeting:</strong> {event_name}</p>
                    <p><strong>Proposed Time:</strong> {formatted_start} IST</p>
                </div>
                
                <div style="background-color: #f8d7da; padding: 15px; border-radius: 6px;">
                    <p style="color: #721c24; margin: 0;">
                        You appear to have another commitment during this time. 
                        Please reply with alternative times that work better for you.
                    </p>
                </div>
            </div>
        </body>
        </html>
        """
        
        return send_enhanced_email(gmail_service, participant_email, subject, plain_body, html_body)
        
    except Exception as e:
        print(f"❌ Error sending conflict notification: {e}")
        return False

def get_event_by_name(calendar_service, event_name, max_results=50):
    """Enhanced function to find event by name with better search"""
    try:
        print(f"🔍 Searching for event: '{event_name}'")
        
        # Get current time and search for events in the next 6 months
        now = datetime.utcnow()
        time_min = (now - timedelta(days=30)).isoformat() + 'Z'  # Include past 30 days
        time_max = (now + timedelta(days=180)).isoformat() + 'Z'  # Next 6 months
        
        # Search for events
        events_result = calendar_service.events().list(
            calendarId='primary',
            timeMin=time_min,
            timeMax=time_max,
            maxResults=max_results,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        print(f"📅 Found {len(events)} total events to search through")
        
        # Search strategies (in order of preference)
        search_strategies = [
            # 1. Exact match (case insensitive)
            lambda e, name: e.get('summary', '').lower() == name.lower(),
            # 2. Event name contains the search term
            lambda e, name: name.lower() in e.get('summary', '').lower(),
            # 3. Search term contains event name (for partial matches)
            lambda e, name: e.get('summary', '').lower() in name.lower(),
            # 4. Fuzzy match - check if words overlap
            lambda e, name: any(word.lower() in e.get('summary', '').lower() 
                              for word in name.split() if len(word) > 2)
        ]
        
        # Try each strategy until we find a match
        for i, strategy in enumerate(search_strategies):
            matches = [event for event in events if strategy(event, event_name)]
            if matches:
                print(f"✅ Found {len(matches)} match(es) using strategy {i+1}")
                # Return the first match, preferring events with attendees
                matches_with_attendees = [e for e in matches if e.get('attendees')]
                if matches_with_attendees:
                    selected_event = matches_with_attendees[0]
                    print(f"🎯 Selected event with attendees: '{selected_event.get('summary')}'")
                else:
                    selected_event = matches[0]
                    print(f"🎯 Selected event: '{selected_event.get('summary')}'")
                
                return selected_event
        
        print(f"❌ No events found matching: '{event_name}'")
        return None
        
    except Exception as error:
        print(f"❌ Error searching for event: {error}")
        return None
    
def extract_delete_details(text):
    """Placeholder for extracting delete details"""
    details = {}
    name_match = re.search(r'delete the (.+?) meeting', text, re.IGNORECASE) or \
                 re.search(r'delete the event (.+)', text, re.IGNORECASE)
    if name_match:
        details['event_name'] = name_match.group(1).strip()
    return details

def prompt_for_deletion_details():
    """Prompt for deletion details"""
    return "Please provide the name of the event you want to delete."


def delete_event(calendar_service, gmail_service, event_name):
    """Enhanced delete event function with better error handling"""
    try:
        print(f"🗑️ Starting deletion process for: '{event_name}'")
        
        # Find the event
        event = get_event_by_name(calendar_service, event_name)
        
        if not event:
            print(f"❌ Event '{event_name}' not found")
            return False
        
        event_id = event['id']
        event_title = event.get('summary', 'Untitled Event')
        
        print(f"🎯 Found event to delete: '{event_title}' (ID: {event_id})")
        
        # Get attendees before deletion for notification
        attendees = event.get('attendees', [])
        print(f"👥 Event has {len(attendees)} attendees")
        
        # Send cancellation emails to attendees first
        user_email = get_authenticated_user_email()
        for attendee in attendees:
            attendee_email = attendee.get('email')
            if attendee_email and attendee_email.lower() != user_email.lower():
                print(f"📧 Sending cancellation email to: {attendee_email}")
                success = send_meeting_cancellation_email(
                    gmail_service,
                    attendee_email,
                    event_title,
                    "Event cancelled by organizer"
                )
                if success:
                    print(f"✅ Cancellation email sent to {attendee_email}")
                else:
                    print(f"⚠️ Failed to send cancellation email to {attendee_email}")
        
        # Now delete the calendar event
        try:
            calendar_service.events().delete(
                calendarId='primary',
                eventId=event_id,
                sendUpdates='all'  # Notify all attendees of deletion
            ).execute()
            
            print(f"✅ Successfully deleted calendar event: '{event_title}'")
            
            # Remove from session tracking if it exists
            try:
                if 'scheduled_meetings' in session:
                    original_count = len(session['scheduled_meetings'])
                    session['scheduled_meetings'] = [
                        m for m in session['scheduled_meetings'] 
                        if m.get('calendar_event_id') != event_id and 
                           m.get('event_name', '').lower() != event_name.lower()
                    ]
                    new_count = len(session['scheduled_meetings'])
                    if new_count < original_count:
                        print(f"🧹 Removed {original_count - new_count} meeting(s) from session tracking")
                        session.modified = True
            except Exception as session_error:
                print(f"⚠️ Error cleaning session data: {session_error}")
            
            return True
            
        except Exception as delete_error:
            error_str = str(delete_error)
            if "404" in error_str:
                print(f"⚠️ Event was already deleted or not found in calendar")
                return True  # Consider this a success since the goal is achieved
            elif "403" in error_str or "forbidden" in error_str.lower():
                print(f"❌ Permission denied: You don't have permission to delete this event")
                return False
            else:
                print(f"❌ Error deleting calendar event: {delete_error}")
                return False
                
    except Exception as error:
        print(f"❌ Error in delete_event function: {error}")
        return False

def send_meeting_cancellation_email(gmail_service, participant_email, event_name, reason=""):
    """Enhanced cancellation email function"""
    try:
        print(f"📧 Preparing cancellation email for: {participant_email}")
        
        subject = f"❌ Meeting Cancelled: {event_name}"
        
        # Enhanced HTML template
        html_body = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: linear-gradient(135deg, #ef4444 0%, #dc2626 100%); color: white; padding: 30px; border-radius: 10px; text-align: center; }}
                .content {{ background-color: #fef2f2; padding: 25px; border-radius: 8px; margin: 25px 0; border-left: 5px solid #dc2626; }}
                .event-name {{ color: #dc2626; margin: 10px 0; font-size: 20px; font-weight: bold; }}
                .reason-box {{ background-color: #fee2e2; padding: 15px; border-radius: 6px; margin: 15px 0; }}
                .footer {{ margin-top: 40px; padding-top: 25px; border-top: 2px solid #e5e7eb; text-align: center; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1 style="margin: 0; font-size: 28px;">❌ Meeting Cancelled</h1>
                    <p style="margin: 10px 0 0 0; font-size: 16px; opacity: 0.9;">We regret to inform you</p>
                </div>
                
                <div class="content">
                    <p style="color: #991b1b; font-size: 16px; margin: 0 0 15px 0;">
                        We regret to inform you that the following meeting has been cancelled:
                    </p>
                    <div class="event-name">{event_name}</div>
                    {f'<div class="reason-box"><p style="margin: 0; color: #991b1b;"><strong>Reason:</strong> {reason}</p></div>' if reason else ""}
                </div>
                
                <div class="footer">
                    <p style="color: #6b7280; font-size: 14px; margin: 0;">
                        We apologize for any inconvenience this may cause.<br>
                        Please contact us if you need to reschedule or have any questions.
                    </p>
                </div>
            </div>
        </body>
        </html>
        """
        
        # Plain text version
        text_body = f"""
❌ MEETING CANCELLED: {event_name}

We regret to inform you that the following meeting has been cancelled:
{event_name}

{f'Reason: {reason}' if reason else ''}

We apologize for any inconvenience this may cause.
Please contact us if you need to reschedule or have any questions.

Best regards,
AI Calendar System
        """
        
        # Send the email
        success = send_enhanced_email(gmail_service, participant_email, subject, text_body, html_body)
        
        if success:
            print(f"✅ Cancellation email sent successfully to {participant_email}")
        else:
            print(f"❌ Failed to send cancellation email to {participant_email}")
            
        return success
        
    except Exception as e:
        print(f"❌ Error sending cancellation email: {e}")
        return False

def extract_update_details(text):
    """Enhanced extraction of update details from text"""
    details = {}
    text_lower = text.lower()
    
    # Extract event name patterns
    event_patterns = [
        r'update\s+(?:the\s+)?(.+?)\s+(?:to|meeting|event)',
        r'change\s+(?:the\s+)?(.+?)\s+(?:to|meeting|event)', 
        r'reschedule\s+(?:the\s+)?(.+?)\s+(?:to|meeting|event)',
        r'modify\s+(?:the\s+)?(.+?)\s+(?:to|meeting|event)',
        r'move\s+(?:the\s+)?(.+?)\s+(?:to|meeting|event)',
        r'(?:update|change|reschedule|modify|move)\s+(.+?)\s+to\s+(.+?)(?:\s+at\s+(.+))?$'
    ]
    
    for pattern in event_patterns:
        match = re.search(pattern, text_lower, re.IGNORECASE)
        if match:
            details['event_name'] = match.group(1).strip()
            if len(match.groups()) > 1 and match.group(2):
                # This might be the new date/time
                potential_datetime = match.group(2).strip()
                if len(match.groups()) > 2 and match.group(3):
                    details['new_date'] = potential_datetime
                    details['new_time'] = match.group(3).strip()
                else:
                    # Try to parse if it contains both date and time
                    if any(time_indicator in potential_datetime for time_indicator in ['am', 'pm', ':', 'morning', 'afternoon', 'evening']):
                        # Contains time info
                        date_time_parts = potential_datetime.split(' at ')
                        if len(date_time_parts) == 2:
                            details['new_date'] = date_time_parts[0].strip()
                            details['new_time'] = date_time_parts[1].strip()
                        else:
                            # Try to split by common time indicators
                            for time_word in ['at', 'from', 'to']:
                                if time_word in potential_datetime:
                                    parts = potential_datetime.split(time_word, 1)
                                    if len(parts) == 2:
                                        details['new_date'] = parts[0].strip()
                                        details['new_time'] = parts[1].strip()
                                        break
                    else:
                        details['new_date'] = potential_datetime
            break
    
    # If event name not found, try simpler patterns
    if 'event_name' not in details:
        simple_patterns = [
            r'(?:meeting|event|appointment)\s+(?:called|named|titled)?\s*["\']?([^"\']+?)["\']?(?:\s+to|\s+from|\s+at|$)',
            r'["\']([^"\']+?)["\']',  # Quoted text
            r'the\s+(.+?)\s+(?:meeting|event|appointment)'
        ]
        
        for pattern in simple_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                potential_name = match.group(1).strip()
                # Filter out common words that aren't event names
                if len(potential_name) > 2 and potential_name.lower() not in ['update', 'change', 'reschedule', 'modify', 'move']:
                    details['event_name'] = potential_name
                    break
    
    # Extract new date separately if not found
    if 'new_date' not in details:
        date_patterns = [
            r'to\s+(tomorrow|today|yesterday)',
            r'to\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)',
            r'to\s+(\d{1,2}[/-]\d{1,2}[/-]?\d{0,4})',
            r'to\s+(\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s*\d{0,4})',
            r'(?:on|for)\s+(tomorrow|today|monday|tuesday|wednesday|thursday|friday|saturday|sunday)',
            r'(?:on|for)\s+(\d{1,2}[/-]\d{1,2}[/-]?\d{0,4})'
        ]
        
        for pattern in date_patterns:
            match = re.search(pattern, text_lower, re.IGNORECASE)
            if match:
                details['new_date'] = match.group(1).strip()
                break
    
    # Extract new time separately if not found
    if 'new_time' not in details:
        time_patterns = [
            r'(?:at|to)\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)(?:\s*(?:to|-)\s*\d{1,2}(?::\d{2})?\s*(?:am|pm))?)',
            r'(?:at|to)\s+(\d{1,2}(?::\d{2})?(?:\s*(?:to|-)\s*\d{1,2}(?::\d{2})?)?)',
            r'(?:from|between)\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?\s*(?:to|-)\s*\d{1,2}(?::\d{2})?\s*(?:am|pm)?)',
            r'(?:at|to)\s+(morning|afternoon|evening|noon)',
        ]
        
        for pattern in time_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                details['new_time'] = match.group(1).strip()
                break
    
    return details

def get_missing_update_field_prompt(current_data):
    """Get the next missing field prompt for updates"""
    missing = []
    
    if 'event_name' not in current_data or not current_data['event_name']:
        missing.append('event_name')
    if 'new_date' not in current_data or not current_data['new_date']:
        missing.append('new_date')
    if 'new_time' not in current_data or not current_data['new_time']:
        missing.append('new_time')
    
    questions = {
        "event_name": "What is the name of the event you want to update? (e.g., 'Team Meeting', 'Project Review')",
        "new_date": "What is the new date for the meeting? (e.g., tomorrow, Friday, March 15)",
        "new_time": "What is the new time for the meeting? (e.g., 2:00 PM to 3:00 PM, 10 AM, morning)"
    }
    
    if missing:
        field = missing[0]
        return field, questions[field]
    return None, None

def validate_update_input(field, user_input):
    """Validate user input for update fields"""
    user_input = user_input.strip()
    
    if field == "event_name":
        return len(user_input) > 0 and user_input.lower() not in ['none', 'null', '']
    
    elif field == "new_date":
        # Try to parse the date
        try:
            parsed = dateparser.parse(user_input, settings={'PREFER_DATES_FROM': 'future'})
            return parsed is not None and parsed.date() >= datetime.now().date()
        except:
            return False
    
    elif field == "new_time":
        # Check if it looks like a time
        time_indicators = ['am', 'pm', ':', 'morning', 'afternoon', 'evening', 'noon']
        return any(indicator in user_input.lower() for indicator in time_indicators) or re.match(r'\d{1,2}', user_input)
    
    return False

def parse_datetime(date_str, time_str):
    """Parse date and time strings into datetime objects with proper timezone"""
    try:
        # Parse date
        parsed_date = dateparser.parse(date_str, settings={'PREFER_DATES_FROM': 'future'})
        if not parsed_date:
            parsed_date = datetime.now() + timedelta(days=1)
        
        # Parse time range
        time_str = time_str.lower().strip()
        
        # Handle natural language time
        time_mappings = {
            'morning': '9:00 AM to 10:00 AM',
            'afternoon': '2:00 PM to 3:00 PM', 
            'evening': '6:00 PM to 7:00 PM',
            'noon': '12:00 PM to 1:00 PM'
        }
        
        for natural, formatted in time_mappings.items():
            if natural in time_str:
                time_str = formatted
                break
        
        # Split time range
        time_parts = re.split(r'\s+(?:to|-|till)\s+', time_str)
        
        if len(time_parts) >= 2:
            start_time_str = time_parts[0].strip()
            end_time_str = time_parts[1].strip()
        else:
            start_time_str = time_parts[0].strip()
            # Default to 1 hour meeting if no end time specified
            start_time = dateparser.parse(f"{parsed_date.date()} {start_time_str}")
            if start_time:
                end_time = start_time + timedelta(hours=1)
                end_time_str = end_time.strftime('%I:%M %p')
            else:
                end_time_str = time_parts[0].strip()
        
        # Parse start time
        start_time = dateparser.parse(f"{parsed_date.date()} {start_time_str}")
        if not start_time:
            start_time = parsed_date.replace(hour=10, minute=0, second=0, microsecond=0)
        
        # Parse end time
        end_time = dateparser.parse(f"{parsed_date.date()} {end_time_str}")
        if not end_time:
            end_time = start_time + timedelta(hours=1)
        
        # Add timezone (IST)
        local_tz = pytz.timezone('Asia/Kolkata')
        
        # Make timezone-naive before localizing
        if start_time.tzinfo is not None:
            start_time = start_time.replace(tzinfo=None)
        if end_time.tzinfo is not None:
            end_time = end_time.replace(tzinfo=None)
            
        # Localize to IST timezone
        start_time = local_tz.localize(start_time)
        end_time = local_tz.localize(end_time)
        
        return start_time, end_time
        
    except Exception as e:
        print(f"Error parsing datetime: {e}")
        # Default to tomorrow 10-11am with timezone
        tomorrow = datetime.now() + timedelta(days=1)
        start = tomorrow.replace(hour=10, minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=1)
        
        local_tz = pytz.timezone('Asia/Kolkata')
        start = local_tz.localize(start)
        end = local_tz.localize(end)
        
        return start, end

def handle_update_workflow(services, user_input, session_data):
    """
    Complete workflow for handling meeting updates
    Returns: (success, message, updated_session_data)
    """
    try:
        if not services or 'gmail' not in services or 'calendar' not in services:
            return False, "❌ Services not available. Please check authentication.", session_data
        
        gmail_service = services['gmail']
        calendar_service = services['calendar']
        
        # Initialize session data if needed
        if 'intent' not in session_data:
            session_data['intent'] = 'update'
        
        if 'data' not in session_data:
            session_data['data'] = {}
        
        data = session_data['data']
        
        # Handle user input for waiting field
        if 'waiting_for' in session_data:
            field = session_data['waiting_for']
            
            if field == "event_name":
                if validate_update_input(field, user_input):
                    data[field] = user_input.strip()
                    del session_data['waiting_for']
                else:
                    return False, "⚠️ Please enter a valid event name.", session_data
            
            elif field == "new_date":
                if validate_update_input(field, user_input):
                    parsed = dateparser.parse(user_input, settings={'PREFER_DATES_FROM': 'future'})
                    if parsed and parsed.date() >= datetime.now().date():
                        data[field] = parsed.strftime('%Y-%m-%d')
                        del session_data['waiting_for']
                    else:
                        return False, "⚠️ Please enter a valid future date (e.g., tomorrow, Friday, March 15).", session_data
                else:
                    return False, "⚠️ Please enter a valid date.", session_data
            
            elif field == "new_time":
                if validate_update_input(field, user_input):
                    data[field] = user_input.strip()
                    del session_data['waiting_for']
                else:
                    return False, "⚠️ Please enter a valid time (e.g., 2:00 PM, 10 AM to 11 AM).", session_data
        
        # If no waiting field, extract from initial input
        else:
            extracted = extract_update_details(user_input)
            data.update(extracted)
        
        # Check for missing fields
        field, prompt = get_missing_update_field_prompt(data)
        if field:
            session_data['waiting_for'] = field
            return False, f"📝 {prompt}", session_data
        
        # All fields present - proceed with update
        event_name = data['event_name']
        new_date = data['new_date']
        new_time = data['new_time']
        
        print(f"🔍 Searching for event: '{event_name}'")
        
        # Find the event
        event = get_event_by_name(calendar_service, event_name)
        if not event:
            # Clear session and return error
            session_data.clear()
            return False, f"❌ Event '{event_name}' not found. Please check the event name and try again.", {}
        
        print(f"✅ Found event: '{event.get('summary', 'Unknown')}'")
        
        # Check if event has attendees
        if 'attendees' not in event or not event['attendees']:
            session_data.clear()
            return False, "⚠️ No attendees found for this event. Cannot update meeting without participants.", {}
        
        # Parse new date/time
        new_start_time, new_end_time = parse_datetime(new_date, new_time)
        
        print(f"📅 New schedule: {new_start_time} to {new_end_time}")
        
        # Get participant emails
        participant_emails = [attendee['email'] for attendee in event['attendees'] if attendee.get('email')]
        
        # Check for conflicts with participants
        conflicts_found = []
        for participant_email in participant_emails:
            if check_participant_calendar_conflicts(calendar_service, participant_email, new_start_time, new_end_time):
                conflicts_found.append(participant_email)
        
        if conflicts_found:
            # Send conflict notifications
            for email in conflicts_found:
                send_conflict_notification(gmail_service, email, event_name, new_start_time, new_end_time)
            
            session_data.clear()
            conflict_msg = f"⚠️ Scheduling conflicts detected for: {', '.join(conflicts_found)}. "
            conflict_msg += "Conflict notifications have been sent to the participants."
            return False, conflict_msg, {}
        
        # Update the event
        updated_event = update_event_with_notification(
            calendar_service, 
            gmail_service, 
            event, 
            new_start_time, 
            new_end_time
        )
        
        if updated_event:
            # Clear session data
            session_data.clear()
            
            success_msg = f"✅ Event '{event_name}' updated successfully!\n\n"
            success_msg += f"📅 New Date: {new_start_time.strftime('%A, %B %d, %Y')}\n"
            success_msg += f"🕐 New Time: {new_start_time.strftime('%I:%M %p')} to {new_end_time.strftime('%I:%M %p')} IST\n\n"
            success_msg += f"📧 Update notifications sent to: {', '.join(participant_emails)}\n"
            success_msg += f"📱 Calendar invitations updated for all attendees."
            
            return True, success_msg, {}
        else:
            session_data.clear()
            return False, "❌ Failed to update the event. Please try again.", {}
            
    except Exception as e:
        print(f"❌ Error in update workflow: {e}")
        session_data.clear()
        return False, f"❌ Error updating meeting: {str(e)}", {}

def create_event_with_proper_invites(calendar_service, gmail_service, summary, start_time, end_time, participant_email, organizer_email='me'):
    """Create calendar event with proper invitations"""
    try:
        # Convert datetime to string if needed
        if hasattr(start_time, 'isoformat'):
            start_time_str = start_time.isoformat()
        else:
            start_time_str = start_time
            
        if hasattr(end_time, 'isoformat'):
            end_time_str = end_time.isoformat()
        else:
            end_time_str = end_time
        
        # Create the event with attendees
        event = {
            'summary': summary,
            'start': {
                'dateTime': start_time_str,
                'timeZone': 'Asia/Kolkata',
            },
            'end': {
                'dateTime': end_time_str,
                'timeZone': 'Asia/Kolkata',
            },
            'attendees': [
                {'email': participant_email, 'responseStatus': 'needsAction'},
            ],
            'reminders': {
                'useDefault': False,
                'overrides': [
                    {'method': 'email', 'minutes': 24 * 60},  # 1 day before
                    {'method': 'popup', 'minutes': 10},
                ],
            },
            'guestsCanInviteOthers': False,
            'guestsCanModify': False,
            'guestsCanSeeOtherGuests': True,
        }
        
        # Create the event and send invitations
        result = calendar_service.events().insert(
            calendarId='primary',
            body=event,
            sendUpdates='all'  # This sends calendar invitations
        ).execute()
        
        # Also send a custom email notification
        if gmail_service:
            send_meeting_invitation_email(
                gmail_service, 
                participant_email, 
                summary, 
                start_time_str, 
                end_time_str,
                result.get('htmlLink', '')  # Calendar event link
            )
        
        print(f"Event created successfully with ID: {result.get('id')}")
        return result
        
    except Exception as e:
        print(f"Error creating event: {e}")
        return None

def send_meeting_invitation_email(gmail_service, participant_email, event_name, start_time, end_time, calendar_link=''):
    """Send comprehensive meeting invitation email"""
    try:
        # Fix: Properly handle timezone parsing
        if isinstance(start_time, str):
            # Handle different timezone formats properly
            if start_time.endswith('Z'):
                # UTC format - convert to IST
                start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                start_dt = start_dt.astimezone(pytz.timezone('Asia/Kolkata'))
            elif '+' in start_time or start_time.endswith('+05:30'):
                # Already has timezone info
                start_dt = datetime.fromisoformat(start_time)
            else:
                # No timezone info - assume it's already in local time
                start_dt = datetime.fromisoformat(start_time)
                if start_dt.tzinfo is None:
                    # Add IST timezone
                    start_dt = pytz.timezone('Asia/Kolkata').localize(start_dt)
        else:
            start_dt = start_time
            
        if isinstance(end_time, str):
            # Handle different timezone formats properly
            if end_time.endswith('Z'):
                # UTC format - convert to IST
                end_dt = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
                end_dt = end_dt.astimezone(pytz.timezone('Asia/Kolkata'))
            elif '+' in end_time or end_time.endswith('+05:30'):
                # Already has timezone info
                end_dt = datetime.fromisoformat(end_time)
            else:
                # No timezone info - assume it's already in local time
                end_dt = datetime.fromisoformat(end_time)
                if end_dt.tzinfo is None:
                    # Add IST timezone
                    end_dt = pytz.timezone('Asia/Kolkata').localize(end_dt)
        else:
            end_dt = end_time
        
        formatted_date = start_dt.strftime('%A, %B %d, %Y')
        formatted_start_time = start_dt.strftime('%I:%M %p')
        formatted_end_time = end_dt.strftime('%I:%M %p')
        
        subject = f"Meeting Invitation: {event_name}"
        
        # Create HTML email for better formatting
        html_body = f"""
        <html>
        <head></head>
        <body>
            <h2>Meeting Invitation</h2>
            <p>Hi,</p>
            <p>You have been invited to a meeting:</p>
            
            <table border="1" cellpadding="10" style="border-collapse: collapse;">
                <tr><td><strong>Event:</strong></td><td>{event_name}</td></tr>
                <tr><td><strong>Date:</strong></td><td>{formatted_date}</td></tr>
                <tr><td><strong>Time:</strong></td><td>{formatted_start_time} to {formatted_end_time} (IST)</td></tr>
            </table>
            
            <p>This meeting has been added to your calendar. Please check your calendar application.</p>
            
            {f'<p><a href="{calendar_link}">View in Google Calendar</a></p>' if calendar_link else ''}
            
            <p>Please reply to this email with:</p>
            <ul>
                <li><strong>"Yes"</strong> to confirm your attendance</li>
                <li><strong>"No"</strong> to decline</li>
            </ul>
            
            <p>Best regards</p>
        </body>
        </html>
        """
        
        # Plain text version
        plain_text_body = f"""
        Meeting Invitation: {event_name}
        
        Hi,
        
        You have been invited to a meeting:
        
        Event: {event_name}
        Date: {formatted_date}
        Time: {formatted_start_time} to {formatted_end_time} (IST)
        
        This meeting has been added to your calendar. Please check your calendar application.
        
        Please reply to this email with:
        - "Yes" to confirm your attendance
        - "No" to decline
        
        Best regards
        """
        
        return send_enhanced_email(
            gmail_service, 
            participant_email, 
            subject, 
            plain_text_body, 
            html_body
        )
        
    except Exception as e:
        print(f"Error sending meeting invitation email: {e}")
        print(f"Debug info - start_time: {start_time}, end_time: {end_time}")
        return False

def suggest_similar_events(calendar_service, event_name):
    """Suggest similar events when exact match not found"""
    try:
        # Get recent events
        start_date = datetime.utcnow() - timedelta(days=30)
        end_date = datetime.utcnow() + timedelta(days=30)
        
        events_result = calendar_service.events().list(
            calendarId='primary',
            timeMin=start_date.isoformat() + 'Z',
            timeMax=end_date.isoformat() + 'Z',
            maxResults=50,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        
        # Find similar events
        suggestions = []
        event_name_words = set(event_name.lower().split())
        
        for event in events:
            summary = event.get('summary', '')
            if summary:
                summary_words = set(summary.lower().split())
                # Check for word overlap
                overlap = len(event_name_words.intersection(summary_words))
                if overlap > 0:
                    suggestions.append({
                        'name': summary,
                        'date': event['start'].get('dateTime', event['start'].get('date')),
                        'overlap_score': overlap
                    })
        
        # Sort by overlap score and return top 3
        suggestions.sort(key=lambda x: x['overlap_score'], reverse=True)
        return suggestions[:3]
        
    except Exception as e:
        print(f"Error suggesting similar events: {e}")
        return []

def format_suggestions_message(suggestions, original_event_name=None):
    """Format suggestions into a user-friendly message"""
    if not suggestions:
        msg = f"No similar events found"
        if original_event_name:
            msg += f" for '{original_event_name}'"
        msg += ". Please check the event name."
        return msg
    
    msg = ""
    if original_event_name:
        msg = f"Event '{original_event_name}' not found. Did you mean one of these?\n\n"
    else:
        msg = "Did you mean one of these?\n\n"
        
    for i, suggestion in enumerate(suggestions, 1):
        date_str = suggestion['date']
        if 'T' in date_str:
            dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            formatted_date = dt.strftime('%Y-%m-%d %I:%M %p')
        else:
            formatted_date = date_str
        
        msg += f"{i}. {suggestion['name']} ({formatted_date})\n"
    
    msg += "\nPlease specify the exact event name you want to update."
    return msg

def get_all_scheduled_meetings():
    """Get all scheduled meetings from session and other sources"""
    meetings = []
    
    # Get from session
    if 'scheduled_meetings' in session:
        meetings.extend(session['scheduled_meetings'])
    
    return meetings

def schedule_meeting(gmail_service, calendar_service, participant_email, event_name, event_date, event_time):
    """Complete meeting scheduling workflow"""
    try:
        print(f"Scheduling meeting: {event_name}")
        print(f"Participant: {participant_email}")
        print(f"Date: {event_date}, Time: {event_time}")
        
        # Parse the datetime
        start_time, end_time = parse_datetime(event_date, event_time)
        print(f"Parsed times - Start: {start_time}, End: {end_time}")
        
        # Check for conflicts (optional)
        has_conflict = check_participant_calendar_conflicts(
            calendar_service, participant_email, start_time, end_time
        )
        
        if has_conflict:
            print("Calendar conflict detected!")
            send_conflict_notification(
                gmail_service, participant_email, event_name, start_time, end_time
            )
            return False
        
        # Create the calendar event with proper invitations
        event_result = create_event_with_proper_invites(
            calendar_service, gmail_service, event_name, start_time, end_time, participant_email
        )
        
        if event_result:
            print("Meeting scheduled successfully!")
            # Store in session for tracking
            if 'scheduled_meetings' not in session:
                session['scheduled_meetings'] = []
            
            meeting_data = {
                'id': str(time.time()),
                'calendar_event_id': event_result.get('id'),
                'event_name': event_name,
                'participant_email': participant_email,
                'start_time': start_time.isoformat(),
                'end_time': end_time.isoformat(),
                'status': 'scheduled'
            }
            
            session['scheduled_meetings'].append(meeting_data)
            session.modified = True
            
            return True
        else:
            print("Failed to create calendar event")
            return False
            
    except Exception as e:
        print(f"Error in schedule_meeting: {e}")
        return False

# Flask Routes
@app.route('/api/chat', methods=['POST'])
def chat_route():
    """Main chat endpoint for handling calendar operations"""
    try:
        global services
        
        if not services:
            return jsonify({"reply": "❌ Please authenticate first. Services not initialized."})
        
        data = request.get_json()
        user_input = data.get("message", "").strip()
        
        if not user_input:
            return jsonify({"reply": "Please enter a message."})
        
        # Initialize session data for conversation state
        if 'intent' not in session:
            session['intent'] = None
        if 'data' not in session:
            session['data'] = {}
        
        # Determine intent
        if session['intent'] is None:
            if is_update_intent(user_input):
                session['intent'] = 'update'
                session['data'] = {}
            elif is_delete_intent(user_input):
                session['intent'] = 'delete'
                session['data'] = {}
            elif is_schedule_intent(user_input):
                session['intent'] = 'schedule'
                session['data'] = {}
            else:
                # Use Gemini for general chat
                try:
                    response = chat.send_message(user_input)
                    return jsonify({"reply": response.text})
                except Exception as e:
                    return jsonify({"reply": f"Error: {str(e)}"})
        
        intent = session['intent']
        
        # Handle UPDATE intent
        if intent == 'update':
            success, message, updated_session = handle_update_workflow(
                services, user_input, dict(session)
            )
            
            # Update session
            session.clear()
            session.update(updated_session)
            session.modified = True
            
            return jsonify({"reply": message})
        
        # Handle DELETE intent
        elif intent == 'delete':
            # Handle user input for missing event name
            if 'waiting_for' in session and session['waiting_for'] == 'event_name':
                event_name = user_input.strip()
                if event_name:
                    session['data']['event_name'] = event_name
                    session.pop('waiting_for', None)
                    session.modified = True
                else:
                    return jsonify({"reply": "⚠️ Please enter a valid event name to delete."})
            
            details = session['data']
            
            # Check if we have event name
            if 'event_name' not in details or not details['event_name']:
                session['waiting_for'] = 'event_name'
                session.modified = True
                return jsonify({"reply": "🗑️ What is the name of the event you want to delete?"})
            
            try:
                event_name = details['event_name']
                
                # Check if event exists first and get details
                event = get_event_by_name(services['calendar'], event_name)
                
                if not event:
                    # Try to suggest similar events
                    suggestions = suggest_similar_events(services['calendar'], event_name)
                    if suggestions:
                        suggestion_msg = format_suggestions_message(suggestions, event_name)
                        session.clear()
                        return jsonify({"reply": f"❌ {suggestion_msg}"})
                    else:
                        session.clear()
                        return jsonify({"reply": f"❌ Event '{event_name}' not found. Please check the event name and try again."})
                
                # Show event details for confirmation
                event_title = event.get('summary', 'Untitled Event')
                attendees = event.get('attendees', [])
                start_time = event.get('start', {}).get('dateTime', event.get('start', {}).get('date', 'Unknown time'))
                
                # Format the confirmation message
                confirmation_msg = f"🗑️ **Confirm Deletion**\n\n"
                confirmation_msg += f"**Event:** {event_title}\n"
                confirmation_msg += f"**Time:** {start_time.split('T')[0] if 'T' in start_time else start_time}\n"
                confirmation_msg += f"**Attendees:** {len(attendees)} participant(s)\n\n"
                
                if attendees:
                    confirmation_msg += "⚠️ **Warning:** This will:\n"
                    confirmation_msg += "• Delete the event from your calendar\n"
                    confirmation_msg += "• Send cancellation emails to all attendees\n"
                    confirmation_msg += "• Remove the event from attendees' calendars\n\n"
                
                # Check if this is a confirmation response
                if 'awaiting_delete_confirmation' in session:
                    if user_input.lower() in ['yes', 'y', 'confirm', 'delete', 'ok']:
                        # Proceed with deletion
                        deleted = delete_event(services['calendar'], services['gmail'], event_name)
                        session.clear()
                        
                        if deleted:
                            success_msg = f"✅ **Event '{event_title}' deleted successfully!**\n\n"
                            if attendees:
                                success_msg += f"📧 Cancellation emails sent to {len(attendees)} attendee(s)\n"
                            success_msg += "🗓️ Event removed from all calendars"
                            return jsonify({"reply": success_msg})
                        else:
                            return jsonify({"reply": f"❌ Failed to delete event '{event_title}'. Please check your permissions or try again."})
                    
                    elif user_input.lower() in ['no', 'n', 'cancel', 'abort']:
                        session.clear()
                        return jsonify({"reply": "❌ Deletion cancelled. The event has not been deleted."})
                    else:
                        return jsonify({"reply": "Please respond with 'yes' to confirm deletion or 'no' to cancel."})
                
                else:
                    # Ask for confirmation
                    session['awaiting_delete_confirmation'] = True
                    session.modified = True
                    confirmation_msg += "❓ **Do you want to proceed with the deletion?** (yes/no)"
                    return jsonify({"reply": confirmation_msg})
                    
            except Exception as e:
                error_msg = str(e)
                print(f"❌ Error in delete workflow: {e}")
                session.clear()
                
                if "403" in error_msg or "permission" in error_msg.lower():
                    return jsonify({"reply": "❌ Permission denied. You don't have permission to delete this event."})
                elif "404" in error_msg:
                    return jsonify({"reply": "❌ Event not found. It may have already been deleted."})
                else:
                    return jsonify({"reply": f"❌ Error deleting event: {error_msg}"})
        
        # Handle SCHEDULE intent
        elif intent == 'schedule':
            # Extract scheduling details from user input
            details = extract_event_details(user_input)
            session['data'].update(details)
            
            # Check for missing required fields
            required_fields = ['participant_email', 'event_name', 'event_date', 'event_time']
            missing_fields = []
            
            for field in required_fields:
                if field not in session['data'] or not session['data'][field]:
                    missing_fields.append(field)
            
            if missing_fields:
                # Ask for the first missing field
                field_prompts = {
                    'participant_email': "What is the participant's email address?",
                    'event_name': "What is the name of the meeting/event?",
                    'event_date': "What date should the meeting be scheduled? (e.g., tomorrow, Friday, March 15)",
                    'event_time': "What time should the meeting be? (e.g., 2:00 PM to 3:00 PM, 10 AM)"
                }
                
                session['waiting_for'] = missing_fields[0]
                session.modified = True
                return jsonify({"reply": f"📝 {field_prompts[missing_fields[0]]}"})
            
            # All fields present - schedule the meeting
            try:
                success = schedule_meeting(
                    services['gmail'],
                    services['calendar'],
                    session['data']['participant_email'],
                    session['data']['event_name'],
                    session['data']['event_date'],
                    session['data']['event_time']
                )
                
                if success:
                    success_msg = f"✅ Meeting '{session['data']['event_name']}' scheduled successfully!\n\n"
                    success_msg += f"👤 Participant: {session['data']['participant_email']}\n"
                    success_msg += f"📅 Date: {session['data']['event_date']}\n"
                    success_msg += f"🕐 Time: {session['data']['event_time']}\n\n"
                    success_msg += "📧 Invitation email sent to participant\n"
                    success_msg += "📱 Calendar event created"
                    
                    session.clear()
                    return jsonify({"reply": success_msg})
                else:
                    session.clear()
                    return jsonify({"reply": "❌ Failed to schedule meeting. Please try again."})
                    
            except Exception as e:
                session.clear()
                return jsonify({"reply": f"❌ Error scheduling meeting: {str(e)}"})
        
        else:
            # Fallback to Gemini chat
            try:
                response = chat.send_message(user_input)
                return jsonify({"reply": response.text})
            except Exception as e:
                return jsonify({"reply": f"Error: {str(e)}"})
                
    except Exception as e:
        print(f"Error in chat route: {e}")
        return jsonify({"reply": f"❌ An error occurred: {str(e)}"})

@app.route('/api/meeting/<meeting_id>/cancel', methods=['POST'])
def cancel_meeting(meeting_id):
    """Enhanced cancel meeting API with better error handling"""
    try:
        print(f"🗑️ API cancellation request for meeting ID: {meeting_id}")
        
        # Get meeting details from all sources
        all_meetings = get_all_scheduled_meetings()
        meeting = next((m for m in all_meetings if m['id'] == meeting_id), None)
        
        if not meeting:
            print(f"❌ Meeting not found: {meeting_id}")
            return jsonify({
                'success': False, 
                'error': f'Meeting with ID {meeting_id} not found'
            })
        
        if not services or 'calendar' not in services:
            return jsonify({
                'success': False, 
                'error': 'Calendar service not available'
            })
        
        event_name = meeting.get('event_name', 'Unknown Event')
        participant_email = meeting.get('participant_email')
        calendar_event_id = meeting.get('calendar_event_id')
        
        print(f"🎯 Cancelling meeting: '{event_name}' with ID: {calendar_event_id}")
        
        # Send cancellation email if participant exists
        email_sent = False
        if participant_email and services.get('gmail'):
            email_sent = send_meeting_cancellation_email(
                services['gmail'],
                participant_email,
                event_name,
                "Meeting cancelled via AI Calendar System"
            )
            print(f"📧 Cancellation email {'sent' if email_sent else 'failed'}")
        
        # Delete from calendar if it has a calendar event ID
        calendar_deleted = False
        if calendar_event_id:
            try:
                services['calendar'].events().delete(
                    calendarId='primary',
                    eventId=calendar_event_id,
                    sendUpdates='all'
                ).execute()
                calendar_deleted = True
                print(f"✅ Deleted from calendar: {calendar_event_id}")
            except Exception as e:
                error_str = str(e)
                if "404" in error_str:
                    calendar_deleted = True  # Already deleted
                    print(f"⚠️ Calendar event already deleted: {calendar_event_id}")
                else:
                    print(f"❌ Error deleting from calendar: {e}")
        else:
            # Try to find and delete by name
            calendar_deleted = delete_event(services['calendar'], services.get('gmail'), event_name)
        
        # Remove from session tracking
        session_meetings = session.get('scheduled_meetings', [])
        original_count = len(session_meetings)
        session['scheduled_meetings'] = [m for m in session_meetings if m['id'] != meeting_id]
        new_count = len(session['scheduled_meetings'])
        
        if new_count < original_count:
            session.modified = True
            print(f"🧹 Removed from session tracking")
        
        # Determine success
        success = calendar_deleted or not calendar_event_id
        
        return jsonify({
            'success': success,
            'message': f'Meeting "{event_name}" {"cancelled successfully" if success else "cancellation completed with some issues"}',
            'details': {
                'calendar_deleted': calendar_deleted,
                'email_sent': email_sent if participant_email else None,
                'session_cleaned': new_count < original_count
            }
        })
        
    except Exception as error:
        print(f"❌ Error in cancel_meeting API: {error}")
        return jsonify({
            'success': False, 
            'error': str(error)
        })

@app.route('/api/debug/events', methods=['GET'])
def debug_events():
    """Debug endpoint to list all events (for troubleshooting)"""
    try:
        if not app.debug:
            return jsonify({'error': 'Debug endpoint only available in debug mode'}), 403
        
        if not services or 'calendar' not in services:
            return jsonify({'error': 'Calendar service not available'}), 500
        
        # Get events from multiple time ranges
        now = datetime.utcnow()
        time_ranges = [
            ("past_week", (now - timedelta(days=7)).isoformat() + 'Z', now.isoformat() + 'Z'),
            ("next_week", now.isoformat() + 'Z', (now + timedelta(days=7)).isoformat() + 'Z'),
            ("next_month", now.isoformat() + 'Z', (now + timedelta(days=30)).isoformat() + 'Z')
        ]
        
        all_events = {}
        
        for range_name, time_min, time_max in time_ranges:
            events_result = services['calendar'].events().list(
                calendarId='primary',
                timeMin=time_min,
                timeMax=time_max,
                maxResults=20,
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            
            events = events_result.get('items', [])
            all_events[range_name] = [
                {
                    'id': event['id'],
                    'summary': event.get('summary', 'No Title'),
                    'start': event.get('start', {}),
                    'attendees_count': len(event.get('attendees', [])),
                    'has_attendees': bool(event.get('attendees')),
                    'status': event.get('status', 'unknown')
                }
                for event in events
            ]
        
        return jsonify({
            'success': True,
            'events_by_range': all_events,
            'total_events': sum(len(events) for events in all_events.values())
        })
        
    except Exception as error:
        return jsonify({'success': False, 'error': str(error)})

@app.route('/api/initialize', methods=['POST'])
def initialize_services():
    """Initialize Google services"""
    try:
        global services
        
        # Initialize the credentials manager
        manager = IntegratedEmailCalendarManager()
        gmail_service, calendar_service = authenticate_services()
        
        services = {
            'gmail': gmail_service,
            'calendar': calendar_service
        }
        
        return jsonify({
            'success': True,
            'message': 'Services initialized successfully'
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        })

# Utility helper functions
def wait_for_acceptance(gmail_service, participant_email, sent_time, timeout=30):
    """Wait for email acceptance (simplified for demo)"""
    # In a real implementation, this would check for replies
    time.sleep(2)  # Simulate processing time
    return True  # Assume accepted for demo

def send_invitation(gmail_service, participant_email, event_date, event_time, event_name=None):
    """Send meeting invitation email (legacy function - kept for compatibility)"""
    try:
        if event_name is None:
            event_name = f"Meeting on {event_date}"
            
        subject = f"Meeting Invitation: {event_name}"
        body = f"""
        Hi,
        
        You have been invited to a meeting:
        Event: {event_name}
        Date: {event_date}
        Time: {event_time}
        
        This meeting has been scheduled in your calendar. Please check your calendar application.
        
        Please reply 'Yes' to confirm or 'No' to decline.
        
        Best regards
        """
        
        message = MIMEText(body)
        message['to'] = participant_email
        message['subject'] = subject
        message['from'] = 'me'
        
        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
        
        result = gmail_service.users().messages().send(
            userId='me',
            body={'raw': raw_message}
        ).execute()
        
        print(f"Invitation sent successfully to {participant_email}")
        return time.time(), True
        
    except Exception as e:
        print(f"Error sending invitation: {e}")
        return time.time(), False

def validate_email_format(email):
    """Validate email format"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def process_natural_language_time(time_text):
    """Process natural language time expressions"""
    try:
        # Handle common time expressions
        time_mappings = {
            'morning': '9:00 AM',
            'afternoon': '2:00 PM',
            'evening': '6:00 PM',
            'noon': '12:00 PM',
            'midnight': '12:00 AM'
        }
        
        # Check for direct mappings
        for key, value in time_mappings.items():
            if key in time_text.lower():
                return value
        
        # Use dateparser for more complex expressions
        parsed = dateparser.parse(time_text)
        if parsed:
            return parsed.strftime('%I:%M %p')
        
        return time_text  # Return original if can't parse
        
    except Exception as e:
        print(f"Error processing natural language time: {e}")
        return time_text

def generate_meeting_summary(event_name, participant_email, date, time):
    """Generate a summary of the meeting details"""
    return f"""
    Meeting Summary:
    ================
    Event: {event_name}
    Participant: {participant_email}
    Date: {date}
    Time: {time}
    Status: Scheduled successfully
    """

# Testing functions
def test_update_extraction():
    """Test the update extraction functionality"""
    test_cases = [
        "Update the team meeting to tomorrow at 3 PM",
        "Change the project review to Friday morning",
        "Reschedule the client call to next Monday from 2 PM to 3 PM",
        "Move the weekly standup to Thursday at 10 AM",
        "Update 'Product Demo' to March 15 at 4 PM",
        "Change the board meeting to next week Tuesday afternoon"
    ]
    
    print("🧪 Testing update detail extraction:")
    print("=" * 50)
    
    for test in test_cases:
        print(f"\nInput: '{test}'")
        result = extract_update_details(test)
        print(f"Output: {result}")
        print("-" * 30)

def test_datetime_parsing():
    """Test datetime parsing functionality"""
    test_cases = [
        ("tomorrow", "2 PM to 3 PM"),
        ("Friday", "morning"),
        ("March 15", "4:00 PM to 5:00 PM"),
        ("next Monday", "10 AM"),
        ("today", "afternoon")
    ]
    
    print("\n🧪 Testing datetime parsing:")
    print("=" * 50)
    
    for date_str, time_str in test_cases:
        print(f"\nDate: '{date_str}', Time: '{time_str}'")
        try:
            start, end = parse_datetime(date_str, time_str)
            print(f"Start: {start}")
            print(f"End: {end}")
        except Exception as e:
            print(f"Error: {e}")
        print("-" * 30)

# Main application initialization
def main():
    """Initialize and run the application"""
    try:
        # Initialize services on startup if credentials exist
        try:
            global services
            manager = IntegratedEmailCalendarManager()
            gmail_service, calendar_service = authenticate_services()
            
            services = {
                'gmail': gmail_service,
                'calendar': calendar_service
            }
            
            print("✅ Services initialized successfully")
            
        except Exception as e:
            print(f"⚠️ Services not initialized on startup: {e}")
            print("Services will need to be initialized via the API endpoint")
        
        # Run the Flask app
        app.run(debug=True, host='0.0.0.0', port=5000)
        
    except Exception as e:
        print(f"❌ Error starting application: {e}")

if __name__ == "__main__":
    main()