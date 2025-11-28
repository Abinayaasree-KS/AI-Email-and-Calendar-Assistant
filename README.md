# AI-Email-and-Calendar-Assistant
Intelligent Automation System for Communication & Scheduling

This project is a complete AI-powered assistant designed to automate Gmail and Google Calendar workflows using Google Workspace APIs integrated with the Gemini AI model. It enables intelligent email handling, automated scheduling, and natural language–based productivity operations through a Flask-based web interface.

The system offers two modes of interaction:

A conversational AI chat assistant

An interactive dashboard for email and calendar management

**Overview**

This application integrates:

Google Gemini AI for natural language processing

Gmail API for reading, analyzing, categorizing, and summarizing emails

Google Calendar API for managing events and meetings

Flask for a lightweight web server with session management and routing

The system automatically extracts meeting requests from emails, detects scheduling conflicts, and sends professional invitations with Google Meet links.

**Objectives
Primary Goals**

Automated Meeting Management: Schedule, update, and delete calendar events using natural language commands.

Intelligent Email Processing: Categorize emails by urgency and type, summarize content, and detect meeting-related requests.

Conflict Detection: Identify overlapping events and provide appropriate notifications or alternative suggestions.

Seamless Integration: Generate Meet links, send structured email invitations, and maintain real-time synchronization with Google Calendar.

**Key Features
Natural Language Processing**

Understands user instructions for scheduling, updating, and canceling meetings

Extracts meeting details such as date, time, participants, and purpose

Generates clean, professional email responses and summaries

**Email Automation**

Categorizes emails based on urgency, intent, and content

Detects meeting requests embedded in email threads

Produces AI-generated summaries for quicker reading

Supports HTML-based email templates for structured communication

**Calendar Automation**

Creates and manages events using Google Calendar API

Detects timing conflicts and suggests alternatives

Generates Google Meet links automatically

Sends calendar invites and updates directly to participants

**Web Interface**

Flask-based dashboard for viewing categorized emails and calendar events

Conversational chat interface powered by Gemini AI

Improved session handling, error control, and API response monitoring

**Technologies Used**

Python (Flask)

Google Gemini AI

Google Gmail API

Google Calendar API

HTML/CSS/JavaScript

OAuth 2.0 Authentication
