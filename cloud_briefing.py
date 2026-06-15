#!/usr/bin/env python3
"""
S Class Auto Aesthetics - Cloud Daily Briefing
Single-file version for GitHub Actions.
Fetches GHL data, builds a styled HTML briefing, and emails it via Gmail SMTP.
"""

import requests
import json
import os
import smtplib
import traceback
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone

API_KEY            = os.environ.get('GHL_API_KEY', '')
LOCATION_ID        = os.environ.get('GHL_LOCATION_ID', '')
EMAIL_FROM         = os.environ.get('EMAIL_FROM', '')
EMAIL_TO           = os.environ.get('EMAIL_TO', '')
GMAIL_APP_PASSWORD = os.environ.get('GMAIL_APP_PASSWORD', '').replace(' ', '')

BASE_URL = 'https://services.leadconnectorhq.com'
HEADERS  = {
    'Authorization': f'Bearer {API_KEY}',
    'Version': '2021-07-28',
    'Content-Type': 'application/json',
    'Accept': 'application/json',
}

def ghl_get(path, params=None):
    url = f'{BASE_URL}{path}'
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f'  [ERROR] {path} -> {e}')
        return {}

def parse_dt(s):
    if not s: return None
    try: return datetime.fromisoformat(s.replace('Z', '+00:00'))
    except: return None

def days_ago(dt):
    if not dt: return None
    return (datetime.now(timezone.utc) - dt).days

def esc(s):
    return (s or '').replace('&','&amp;').replace('<','&lt;').replace('>','&gt;').replace('"','&quot;')

def get_new_leads(hours=24):
    data = ghl_get('/contacts/', {'locationId': LOCATION_ID, 'limit': 100, 'sortBy': 'date_added'})
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    return [c for c in data.get('contacts', []) if (parse_dt(c.get('dateAdded') or c.get('createdAt')) or datetime.min.replace(tzinfo=timezone.utc)) > cutoff]

def get_stale_uncontacted_leads(hours=5):
    data = ghl_get('/contacts/', {'locationId': LOCATION_ID, 'limit': 100, 'sortBy': 'date_added'})
    cutoff_old = datetime.now(timezone.utc) - timedelta(hours=hours)
    cutoff_new = datetime.now(timezone.utc) - timedelta(hours=72)
    result = []
    for c in data.get('contacts', []):
        dt = parse_dt(c.get('dateAdded') or c.get('createdAt'))
        if dt and cutoff_new < dt < cutoff_old:
            last_activity = parse_dt(c.get('lastActivityDate'))
            if not last_activity or last_activity <= dt:
                result.append(c)
    return result

def get_pipeline_stage_lookup():
    data = ghl_get('/opportunities/pipelines', {'locationId': LOCATION_ID})
    lookup = {}
    for pipeline in data.get('pipelines', []):
        for stage in pipeline.get('stages', []):
            sid = stage.get('id') or stage.get('_id')
            if sid: lookup[sid] = stage.get('name', 'Unknown')
    return lookup

def get_pipeline_opportunities():
    stage_lookup = get_pipeline_stage_lookup()
    data = ghl_get('/opportunities/search', {'location_id': LOCATION_ID, 'limit': 20, 'status': 'open'})
    opps = data.get('opportunities', [])
    for opp in opps:
        stage_id = opp.get('pipelineStageId')
        if stage_id and stage_id in stage_lookup:
            opp['_resolvedStageName'] = stage_lookup[stage_id]
    return opps

def get_todays_appointments():
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    cal_data = ghl_get('/calendars/', {'locationId': LOCATION_ID})
    all_events = []
    for cal in cal_data.get('calendars', []):
        cal_id = cal.get('id')
        if not cal_id: continue
        data = ghl_get('/calendars/events', {'locationId': LOCATION_ID, 'calendarId': cal_id, 'startTime': int(start.timestamp()*1000), 'endTime': int(end.timestamp()*1000)})
        all_events.extend(data.get('events', []))
    return all_events

def get_unread_conversations():
    data = ghl_get('/conversations/search', {'locationId': LOCATION_ID, 'limit': 20, 'unreadOnly': True})
    return data.get('conversations', [])

def fmt_name(c):
    first = c.get('firstName') or ''
    last = c.get('lastName') or ''
    if first.lower() == 'none': first = ''
    if last.lower() == 'none': last = ''
    name = f"{first}{' ' + last if last else ''}".strip()
    return name or c.get('phone') or c.get('mobile') or 'Unknown'

def fmt_phone(c):
    return c.get('phone') or c.get('mobile') or 'No phone'

def fmt_source(c):
    return c.get('source') or c.get('attributionSource', {}).get('medium') or 'Unknown'

def fmt_appt_time(s):
    dt = parse_dt(s)
    if not dt: return s or '?'
    local = dt - timedelta(hours=4)
    return local.strftime('%I:%M %p')

def build_html_email():
    now_et = datetime.now(timezone.utc) - timedelta(hours=4)
    date_display = now_et.strftime('%A, %B %d')
    time_display = now_et.strftime('%I:%M %p')
    new_leads = get_new_leads(24)
    stale     = get_stale_uncontacted_leads(5)
    unreads   = get_unread_conversations()
    opps      = get_pipeline_opportunities()
    appts     = get_todays_appointments()
    BG='#0b0c0e'; PANEL='#141619'; LINE='#2a2e34'; INK='#eceef1'; MUTE='#7e858f'
    GOLD='#f0a830'; GREEN='#4cc38a'; RED='#e5564c'; BLUE='#5b8def'; PURPLE='#b388ff'

    def vital(num, label, color):
        return f'<td style="background:{PANEL};border:1px solid {LINE};border-radius:10px;padding:10px 12px;text-align:center;width:25%"><div style="font-size:22px;font-weight:800;color:{color}">{num}</div><div style="font-size:9px;letter-spacing:.1em;text-transform:uppercase;color:{MUTE};margin-top:2px">{label}</div></td>'

    def section_label(text, count, color):
        return f'<tr><td colspan="3" style="padding:18px 0 8px 0"><span style="font-size:11px;letter-spacing:.12em;text-transform:uppercase;font-weight:700;color:{color}">{text}</span><span style="background:#1b1e22;border:1px solid {LINE};border-radius:6px;padding:2px 7px;font-size:12px;font-weight:800;color:{INK};margin-left:8px">{count}</span></td></tr>'

    def card_row(*cells):
        cell_html = ''.join(f'<td style="{style}">{content}</td>' for content, style in cells)
        return f'<tr><td colspan="3" style="padding:0 0 6px 0"><table width="100%" cellpadding="0" cellspacing="0" style="background:{PANEL};border:1px solid {LINE};border-radius:10px"><tr>{cell_html}</tr></table></td></tr>'

    vitals_html = f'<table width="100%" cellpadding="0" cellspacing="0" style="border-spacing:8px 0;margin:14px 0"><tr>{vital(len(new_leads),"New leads",GREEN)}<td style="width:8px"></td>{vital(len(stale),"Need call",RED if stale else GREEN)}<td style="width:8px"></td>{vital(len(unreads),"Unreads",GOLD if len(unreads)>5 else BLUE)}<td style="width:8px"></td>{vital(len(opps),"Pipeline",GOLD)}<td style="width:8px"></td>{vital(len(appts),"Appts",PURPLE)}</tr></table>'

    body_rows = ''
    if stale:
        body_rows += section_label('Call now - no response', len(stale), RED)
        for c in stale[:5]:
            dt = parse_dt(c.get('dateAdded') or c.get('createdAt'))
            hrs = round((datetime.now(timezone.utc) - dt).total_seconds() / 3600, 1) if dt else '?'
            body_rows += card_row((esc(fmt_name(c)), f'padding:11px 13px;font-size:14px;font-weight:600;color:{INK}'), (esc(fmt_phone(c)), f'padding:11px 8px;font-size:12px;color:{MUTE};font-family:monospace'), (f'{hrs}h ago', f'padding:11px 13px;font-size:11px;color:{RED};text-align:right;white-space:nowrap'))

    body_rows += section_label('New leads (24h)', len(new_leads), GREEN)
    if new_leads:
        for c in new_leads[:8]:
            body_rows += card_row((esc(fmt_name(c)), f'padding:11px 13px;font-size:14px;font-weight:600;color:{INK}'), (esc(fmt_phone(c)), f'padding:11px 8px;font-size:12px;color:{MUTE};font-family:monospace'), (f'via {esc(fmt_source(c))}', f'padding:11px 13px;font-size:11px;color:{MUTE};text-align:right'))
    else:
        body_rows += f'<tr><td colspan="3" style="padding:8px 0;font-size:13px;color:{MUTE};font-style:italic">None in the last 24 hours.</td></tr>'

    body_rows += section_label('Unread conversations', len(unreads), BLUE)
    if unreads:
        for conv in unreads[:8]:
            name = conv.get('contactName') or conv.get('fullName') or 'Unknown'
            msg = (conv.get('lastMessageBody') or '')[:65]
            body_rows += card_row((esc(name), f'padding:11px 13px;font-size:14px;font-weight:600;color:{INK};white-space:nowrap'), (f'""{esc(msg)}...""', f'padding:11px 8px;font-size:12px;color:{MUTE};overflow:hidden'), ('', f'width:1px'))
    else:
        body_rows += f'<tr><td colspan="3" style="padding:8px 0;font-size:13px;color:{GREEN}">Inbox clear.</td></tr>'

    stage_lookup = get_pipeline_stage_lookup()
    body_rows += section_label('Pipeline deals', len(opps), GOLD)
    if opps:
        for o in opps[:8]:
            name = o.get('contact',{}).get('name') or o.get('name') or 'Unknown'
            stage = stage_lookup.get(o.get('pipelineStageId') or o.get('stageId'), 'Unknown')
            val = o.get('monetaryValue')
            val_display = f'${float(val):,.0f}' if val else ''
            body_rows += card_row((esc(name), f'padding:11px 13px;font-size:14px;font-weight:600;color:{INK}'), (esc(stage), f'padding:11px 8px;font-size:12px;color:{GOLD}'), (val_display, f'padding:11px 13px;font-size:13px;font-weight:700;color:{GREEN};text-align:right'))
    else:
        body_rows += f'<tr><td colspan="3" style="padding:8px 0;font-size:13px;color:{MUTE};font-style:italic">No active deals.</td></tr>'

    body_rows += section_label("Today's appointments", len(appts), PURPLE)
    if appts:
        for a in appts[:6]:
            title = a.get('title') or a.get('name') or 'Appointment'
            start = a.get('startTime') or a.get('start')
            t = ''
            if start:
                try:
                    dt = datetime.fromisoformat(start.replace('Z','+00:00'))
                    dt_et = dt - timedelta(hours=4)
                    t = dt_et.strftime('%I:%M %p')
                except: t = ''
            body_rows += card_row((esc(title), f'padding:11px 13px;font-size:14px;font-weight:600;color:{INK}'), (t, f'padding:11px 8px;font-size:13px;color:{PURPLE};font-weight:600'), ('', f'width:1px'))
    else:
        body_rows += f'<tr><td colspan="3" style="padding:8px 0;font-size:13px;color:{MUTE};font-style:italic">No appointments today.</td></tr>'

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head><body style="margin:0;padding:0;background:{BG};font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="background:{BG}"><tr><td align="center" style="padding:28px 12px">
<table width="600" cellpadding="0" cellspacing="0">
<tr><td style="text-align:center;padding-bottom:18px"><div style="display:inline-block;background:linear-gradient(135deg,{GOLD},#d4922a);width:44px;height:44px;border-radius:12px;line-height:44px;font-size:18px;font-weight:900;color:{BG};letter-spacing:1px">BT</div><div style="font-size:18px;font-weight:700;color:{INK};margin-top:6px">S Class Auto Aesthetics</div><div style="font-size:11px;color:{MUTE};letter-spacing:.08em;text-transform:uppercase;margin-top:2px">Daily Briefing &mdash; {date_display} &middot; {time_display}</div></td></tr>
<tr><td>{vitals_html}</td></tr>
<tr><td style="padding-top:12px"><table width="100%" cellpadding="0" cellspacing="0">{body_rows}</table></td></tr>
<tr><td style="padding-top:24px;text-align:center;font-size:10px;color:{MUTE}">Automated via GitHub Actions &middot; GHL API v2</td></tr>
</table></td></tr></table></body></html>"""
    return html


def send_email(html):
    msg = MIMEMultipart('alternative')
    msg['Subject'] = f"S Class Briefing - {datetime.now(timezone.utc).strftime('%b %d')}"
    msg['From']    = EMAIL_FROM
    msg['To']      = EMAIL_TO
    msg.attach(MIMEText(html, 'html'))
    with smtplib.SMTP('smtp.gmail.com', 587) as s:
        s.starttls()
        s.login(EMAIL_FROM, GMAIL_APP_PASSWORD)
        s.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
    print(f'Email sent to {EMAIL_TO}')


if __name__ == '__main__':
    html = build_html_email()
    send_email(html)
