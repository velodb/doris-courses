"""Portable, UI-only course-service guidance shared by the portal and gateway."""
from html import escape
from urllib.parse import urlsplit
from i18n import InstalledUI

RTA = 'doris-course/01-real-time-analytics/'
SINGLE = {'lab': RTA + 'level1/module01-introduction/lab01_start_doris_and_build_baseline.ipynb'}
MULTI = {'lab': RTA + 'level3/module09-sharding-replication/lab09_observe_sharding_and_replication.ipynb'}


def details(service_id, *, messages=None):
    t = messages or InstalledUI().context().messages
    group = 'single' if service_id == 'rta-fe' else 'multi' if service_id in {'rta-multi-fe', 'rta-fe1', 'rta-fe2', 'rta-fe3'} else None
    if group is None:
        return {}
    row = dict(SINGLE if group == 'single' else MULTI)
    row.update({field: t(f'service.{group}.{field}') for field in ('description', 'startup')})
    row['lab_label'] = t(f'service.{group}.lab')
    if group == 'single':
        row['label'] = t('service.single.label')
    elif service_id != 'rta-multi-fe':
        row['label'] = 'FE ' + service_id[-1]
    return row


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


def unavailable_page(service_id, *, origin, workspace, assets, context=None):
    ctx = context or InstalledUI().context()
    t, h = ctx.messages, ctx.messages.html
    row = details(service_id, messages=t)
    label = row.get('label', t('service.label'))
    title = t('service.title', service=label)
    description = row.get('description', t('service.description'))
    startup = row.get('startup', t('service.startup'))
    if service_id == 'jupyter':
        description, startup = t('service.jupyter.description'), t('service.jupyter.startup')
    links = []
    if workspace and row.get('lab'):
        links.append(f'<a class="primary" href="{safe_url(ctx.url(lab_url(workspace, row["lab"])))}" target="_blank" rel="noopener">{escape(row["lab_label"])}</a>')
    links.append(f'<a class="secondary" href="{safe_url(ctx.url(origin + "/"))}">{h("state.not-ready.primary")}</a>')
    if workspace and service_id != 'jupyter':
        links.append(f'<a class="text-link" href="{safe_url(ctx.url(workspace))}">{h("template.lesson.text-5")}</a>')
    home = ctx.url(workspace or origin + '/')
    return (f'<!doctype html><html lang="{escape(ctx.locale)}" dir="{ctx.direction}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{escape(title)} · {h("template.page.text-4")}</title><link rel="stylesheet" href="{safe_url(assets + "/ui.css")}"></head>'
        f'<body data-workshop-ui="1"><header class="topbar"><a class="brand" href="{safe_url(home)}"><img src="{safe_url(assets + "/logo.svg")}" alt="{h("template.page.text-2")}"><span>{h("template.page.text-3")}</span></a></header>'
        f'<main class="main"><section class="state"><div class="eyebrow">{h("service.label")}</div><h1>{escape(title)}</h1>'
        f'<p>{escape(description)}</p><div class="state-detail"><p>{escape(startup)}</p>'
        f'<p>{h("service.check")}</p></div>'
        f'<div class="actions">{"".join(links)}</div></section></main></body></html>')
