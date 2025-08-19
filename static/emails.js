let allEmails = [];

// Load emails when page loads
document.addEventListener('DOMContentLoaded', function() {
    loadEmails();
    setupFilters();
});

async function loadEmails() {
    const loading = document.getElementById('loading');
    const container = document.getElementById('emails-container');
    
    loading.classList.remove('hidden');
    
    try {
        const response = await fetch('/api/emails');
        const data = await response.json();
        
        if (data.success) {
            allEmails = data.emails;
            displayEmails(allEmails);
            updateStats(allEmails);
        } else {
            showError('Failed to load emails: ' + data.error);
        }
    } catch (error) {
        console.error('Error loading emails:', error);
        showError('Network error occurred');
    } finally {
        loading.classList.add('hidden');
    }
}

function displayEmails(emails) {
    const container = document.getElementById('emails-container');
    const loading = document.getElementById('loading');
    
    if (emails.length === 0) {
        container.innerHTML = '<div class="no-emails">📭 No emails found</div>';
        return;
    }
    
    const emailsHTML = emails.map(email => createEmailCard(email)).join('');
    container.innerHTML = emailsHTML;
}

function createEmailCard(email) {
    const urgencyClass = `urgency-${email.ai_urgency}`;
    const actionIcon = email.action_required ? '⚡' : '';
    const confidencePercent = Math.round(email.confidence * 100);
    
    return `
        <div class="email-card ${email.ai_urgency}" onclick="openEmailDetails('${email.id}')">
            <div class="email-header">
                <div>
                    <div class="email-subject">${actionIcon} ${email.subject}</div>
                    <div class="email-sender">From: ${email.sender}</div>
                </div>
                <div class="email-urgency ${urgencyClass}">${email.ai_urgency.toUpperCase()}</div>
            </div>
            <div class="email-summary">${email.ai_summary}</div>
            <div class="email-meta">
                <span>📂 ${email.ai_category} | 🧠 ${email.ai_reason}</span>
                <span class="ai-confidence">AI Confidence: ${confidencePercent}%</span>
            </div>
        </div>
    `;
}

function updateStats(emails) {
    const totalEmails = emails.length;
    const urgentEmails = emails.filter(e => e.ai_urgency === 'high').length;
    const actionEmails = emails.filter(e => e.action_required).length;
    
    document.getElementById('total-emails').textContent = totalEmails;
    document.getElementById('urgent-emails').textContent = urgentEmails;
    document.getElementById('action-emails').textContent = actionEmails;
}

function setupFilters() {
    const filterBtns = document.querySelectorAll('.filter-btn');
    
    filterBtns.forEach(btn => {
        btn.addEventListener('click', function() {
            // Update active button
            filterBtns.forEach(b => b.classList.remove('active'));
            this.classList.add('active');
            
            // Filter emails
            const filter = this.dataset.filter;
            const filteredEmails = filterEmails(allEmails, filter);
            displayEmails(filteredEmails);
        });
    });
}

function filterEmails(emails, filter) {
    if (filter === 'all') return emails;
    if (filter === 'action') return emails.filter(e => e.action_required);
    return emails.filter(e => e.ai_urgency === filter);
}

async function refreshEmails() {
    await loadEmails();
    // Reset filter to 'all'
    document.querySelector('.filter-btn[data-filter="all"]').click();
}

function openEmailDetails(emailId) {
    // For now, just log - we'll expand this tomorrow
    console.log('Opening email:', emailId);
    alert('Email details view - Coming in Day 2! 📧');
}

function showError(message) {
    const container = document.getElementById('emails-container');
    container.innerHTML = `
        <div style="text-align: center; color: #ff4444; padding: 40px;">
            ⚠️ ${message}
        </div>
    `;
}