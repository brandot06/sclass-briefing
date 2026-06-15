#!/usr/bin/env python3
"""
Fetches GHL data and writes pulse_data.js for the command center dashboard.
Runs as part of GitHub Actions alongside cloud_briefing.py.
"""

import requests
import json
import os
from datetime import datetime, timedelta, timezone

API_KEY     = os.environ.get('GHL_API_KEY', '')
LOCATION_ID = os.environ.get('GHL_LOCATION_ID', '')
BASE_URL    = 'https://services.leadconnectorhq.com'
HEADERS     = {
    'Authorization': f'Bearer {API_KEY}',
    'Version': '2021-07-28',
    'Content-Type': 'application/json',
    'Accept': 'application/json',
}

def ghl_get(path, params=None):
    try:
        r = requests.get(f'{BASE_URL}{path}', headers=HEADERS, params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f'  [WARN] {path} → {e}')
        return {}

def parse_dt(s):
    if not s: return None
    try: return datetime.fromisoformat(s.replace('Z', '+00:00'))
    except: return None

def fmt_name(c):
    first = c.get('firstName') or ''
    last  = c.get('lastName') or ''
    if first.lower() == 'none': first = ''
    if last.lower() == 'none':  last = ''
    name = f"{first}{' ' + last if last else ''}".strip()
    return name or c.get('phone') or c.get('mobile') or 'Unknown'

def fmt_phone(c):
    return c.get('phone') or c.get('mobile') or ''

def fmt_source(c):
    return c.get('source') or c.get('attributionSource', {}).get('medium') or 'Unknown'

def get_new_leads(hours=24):
    data = ghl_get('/contacts/', {'locationId': LOCATION_ID, 'limit': 100, 'sortBy': 'date_added'})
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    return [c for c in data.get('contacts', [])
            if (parse_dt(c.get('dateAdded') or c.get('createdAt')) or datetime.min.replace(tzinfo=timezone.utc)) > cutoff]

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
    data = ghl_get('/opportunities/search', {'location_id': LOCATION_ID, 'limit': 20, 'status': 'open'})
    return data.get('opportunities', [])

def get_todays_appointments():
    now   = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end   = start + timedelta(days=1)
    cal_data = ghl_get('/calendars/', {'locationId': LOCATION_ID})
    events = []
    for cal in cal_data.get('calendars', []):
        cid = cal.get('id')
        if not cid: continue
        data = ghl_get('/calendars/events', {
            'locationId': LOCATION_ID, 'calendarId': cid,
            'startTime': int(start.timestamp() * 1000),
            'endTime': int(end.timestamp() * 1000),
        })
        events.extend(data.get('events', []))
    return events

def get_unread_conversations():
    data = ghl_get('/conversations/search', {'locationId': LOCATION_ID, 'limit': 20, 'unreadOnly': True})
    return data.get('conversations', [])

def build_pulse_data():
    now_et = datetime.now(timezone.utc) - timedelta(hours=4)

    new_leads = get_new_leads(24)
    stale     = get_stale_uncontacted_leads(5)
    unreads   = get_unread_conversations()
    opps      = get_pipeline_opportunities()
    appts     = get_todays_appointments()
    stage_lookup = get_pipeline_stage_lookup()

    pulse = {
        'updatedDisplay': now_et.strftime('%b %d, %I:%M %p ET'),
        'updatedISO': datetime.now(timezone.utc).isoformat(),
        'counts': {
            'newLeads': len(new_leads),
            'staleLeads': len(stale),
            'unreads': len(unreads),
            'pipeline': len(opps),
            'appointments': len(appts),
        },
        'newLeads': [],
        'staleLeads': [],
        'unreads': [],
        'pipeline': [],
        'appointments': [],
    }

    for c in new_leads[:10]:
        pulse['newLeads'].append({
            'name': fmt_name(c),
            'phone': fmt_phone(c),
            'source': fmt_source(c),
        })

    for c in stale[:8]:
        dt = parse_dt(c.get('dateAdded') or c.get('createdAt'))
        hrs = round((datetime.now(timezone.utc) - dt).total_seconds() / 3600, 1) if dt else 0
        pulse['staleLeads'].append({
            'name': fmt_name(c),
            'phone': fmt_phone(c),
            'hoursAgo': hrs,
        })

    for conv in unreads[:10]:
        pulse['unreads'].append({
            'name': conv.get('contactName') or conv.get('fullName') or 'Unknown',
            'lastMsg': (conv.get('lastMessageBody') or '')[:80],
        })

    for o in opps[:10]:
        stage_id = o.get('pipelineStageId') or o.get('stageId')
        stage_name = stage_lookup.get(stage_id, 'Unknown')
        val = o.get('monetaryValue')
        contact_name = o.get('contact', {}).get('name') or o.get('name') or 'Unknown'
        updated = parse_dt(o.get('lastStatusChangeAt') or o.get('updatedAt'))
        days_stale = (datetime.now(timezone.utc) - updated).days if updated else 0
        pulse['pipeline'].append({
            'name': contact_name,
            'stage': stage_name,
            'value': float(val) if val else 0,
            'daysStale': days_stale,
        })

    for a in appts[:8]:
        title = a.get('title') or a.get('name') or 'Appointment'
        start = a.get('startTime') or a.get('start')
        t = ''
        if start:
            try:
                dt = datetime.fromisoformat(start.replace('Z', '+00:00'))
                dt_et = dt - timedelta(hours=4)
                t = dt_et.strftime('%I:%M %p')
            except: pass
        contact = a.get('contact', {}).get('name') or a.get('contactName') or ''
        pulse['appointments'].append({
            'title': title,
            'time': t,
            'contact': contact,
        })

    return pulse

if __name__ == '__main__':
    print('Fetching GHL data for pulse...')
    pulse = build_pulse_data()
    js_content = f'window.PULSE_DATA = {json.dumps(pulse, indent=2)};'
    with open('pulse_data.js', 'w') as f:
        f.write(js_content)
    print(f'pulse_data.js written — {len(js_content)} bytes')
    print(f'  New leads: {pulse["counts"]["newLeads"]}')
    print(f'  Stale: {pulse["counts"]["staleLeads"]}')
    print(f'  Unreads: {pulse["counts"]["unreads"]}')
    print(f'  Pipeline: {pulse["counts"]["pipeline"]}')
    print(f'  Appointments: {pulse["counts"]["appointments"]}')
