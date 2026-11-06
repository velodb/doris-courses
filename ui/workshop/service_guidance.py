"""Portable, UI-only course-service guidance shared by the portal and gateway."""
from html import escape
from urllib.parse import urlsplit

RTA = 'doris-course/01-real-time-analytics/'
SINGLE = {'label': 'Doris · single FE', 'description': 'Single-node sandbox · 1 FE + 1 BE. Start it in Lab 1.',
          'lab': RTA + 'level1/module01-introduction/lab01_start_doris_and_build_baseline.ipynb', 'lab_label': 'Open Lab 1',
          'startup': 'Run the Doris startup cell in Lab 1 and wait for it to finish. Then try opening this page again.'}
MULTI = {'description': 'Lab 9 only · a separate cluster with 3 FEs + 3 BEs. Follow Lab 9 to stop the single-node sandbox and start this cluster before opening FE 1–3.',
         'lab': RTA + 'level3/module09-sharding-replication/lab09_observe_sharding_and_replication.ipynb', 'lab_label': 'Open Lab 9',
         'startup': 'Open Lab 9 and follow its cluster startup steps, including stopping the single-node sandbox. Wait for the startup cells to finish, then try this FE again.'}

def details(service_id):
    if service_id == 'rta-fe':
        return dict(SINGLE)
    if service_id == 'rta-multi-fe':
        return dict(MULTI)
    if service_id in {'rta-fe1', 'rta-fe2', 'rta-fe3'}:
        return {**MULTI, 'label': 'FE ' + service_id[-1]}
    return {}


def safe_url(value):
    parts = urlsplit(value)
    if (any(ord(c) < 32 or c == '\\' for c in value) or parts.username or parts.password or
            (parts.scheme and (parts.scheme != 'https' or not parts.hostname)) or
            (not parts.scheme and (parts.netloc or not parts.path.startswith('/')))):
        raise ValueError('Invalid service guidance URL')
    return escape(value, quote=True)


def lab_url(course_url, notebook):
    parts = urlsplit(course_url)
    prefix = f'{parts.scheme}://{parts.netloc}' if parts.scheme else ''
    return prefix + '/lab/tree/' + notebook


def unavailable_page(service_id, *, origin, workspace, assets):
    row = details(service_id)
    label = row.get('label', 'Course service')
    title = label + ' is not responding'
    description = row.get('description', 'This service is started from a course Lab.')
    startup = row.get('startup', 'Open your current Lab, run its service-start cells, and wait for them to finish. Then try this page again.')
    if service_id == 'jupyter':
        description = 'Your Notebook workspace may still be starting or temporarily unavailable.'
        startup = 'Wait a moment, then try again. Contact your organizer if the workspace stays unavailable.'
    links = []
    if workspace and row.get('lab'):
        links.append(f'<a class="primary" href="{safe_url(lab_url(workspace, row["lab"]))}" target="_blank" rel="noopener">{escape(row["lab_label"])}</a>')
    links.append(f'<a class="secondary" href="{safe_url(origin + "/")}">Try opening again</a>')
    if workspace and service_id != 'jupyter':
        links.append(f'<a class="text-link" href="{safe_url(workspace)}">Back to course</a>')
    home = workspace or origin + '/'
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{escape(title)} · Apache Doris Workshop</title><link rel="stylesheet" href="{safe_url(assets + "/ui.css")}"></head>'
        f'<body data-workshop-ui="1"><header class="topbar"><a class="brand" href="{safe_url(home)}"><img src="{safe_url(assets + "/logo.svg")}" alt="Apache Doris"><span>WORKSHOP</span></a></header>'
        f'<main class="main"><section class="state"><div class="eyebrow">Course service</div><h1>{escape(title)}</h1>'
        f'<p>{escape(description)}</p><div class="state-detail"><p>{escape(startup)}</p>'
        '<p>If you already started the service, check the latest Lab cell output. It may still be starting or may have stopped.</p></div>'
        f'<div class="actions">{"".join(links)}</div></section></main></body></html>')
