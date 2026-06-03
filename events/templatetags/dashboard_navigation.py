from django import template
from django.urls import NoReverseMatch, reverse

register = template.Library()


def _reverse(name, *args, **kwargs):
    try:
        return reverse(name, args=args, kwargs=kwargs)
    except NoReverseMatch:
        return ''


def _event_label(event, fallback):
    return getattr(event, 'title', '') or fallback


def _object_label(obj, fallback):
    return getattr(obj, 'name', '') or getattr(obj, 'title', '') or fallback


def _event_from_context(context):
    event = context.get('event')
    if event:
        return event

    obj = context.get('object')
    if obj and getattr(obj, 'kind', None):
        return obj

    if obj and getattr(obj, 'event', None):
        return obj.event

    return None


def _with_current(crumbs):
    if crumbs:
        crumbs[-1]['url'] = ''
    return crumbs


def _back_from_crumbs(crumbs):
    for crumb in reversed(crumbs[:-1]):
        if crumb.get('url'):
            return crumb
    return None


@register.simple_tag(takes_context=True)
def dashboard_page_meta(context):
    request = context.get('request')
    match = getattr(request, 'resolver_match', None)
    url_name = getattr(match, 'url_name', '') if match else ''
    event = _event_from_context(context)
    obj = context.get('object')

    home = {'label': 'Dashboard', 'url': _reverse('dashboard:home')}
    crumbs = [home.copy()]
    title = 'Dashboard'
    subtitle = 'Use search, notifications, and your profile tools from this header.'
    back_label = ''
    back_url = ''

    simple_pages = {
        'analytics': ('Analytics', 'Track votes, revenue, and performance across your events.', 'dashboard:analytics'),
        'profile': ('Profile', 'Manage your organizer account and security settings.', 'dashboard:profile'),
        'notifications': ('Notifications', 'Review recent account and event updates.', 'dashboard:notifications'),
        'revenue': ('Revenue & Earnings', 'Monitor confirmed sales, fees, and organizer earnings.', 'dashboard:revenue'),
        'withdrawals': ('Withdrawals', 'Request payouts from your available balance.', 'dashboard:withdrawals'),
    }

    if url_name == 'home':
        title = 'Dashboard'
        subtitle = "Here's what's happening with your events today."
    elif url_name in {'competitions', 'my_events'}:
        title = context.get('title') or 'My Competitions'
        subtitle = context.get('subtitle') or 'Manage, monitor, and publish your voting competitions.'
        crumbs.append({'label': 'Competitions', 'url': _reverse('dashboard:competitions')})
    elif url_name == 'elections':
        title = context.get('title') or 'Secure Elections'
        subtitle = context.get('subtitle') or 'Configure eligible voters, candidate positions, and secure tallies.'
        crumbs.append({'label': 'Elections', 'url': _reverse('dashboard:elections')})
    elif url_name in simple_pages:
        title, subtitle, route = simple_pages[url_name]
        if url_name == 'withdrawals':
            crumbs.append({'label': 'Revenue', 'url': _reverse('dashboard:revenue')})
        crumbs.append({'label': title, 'url': _reverse(route)})
    elif url_name == 'notification_settings':
        title = 'Notification Settings'
        subtitle = 'Choose how Vootely reaches you about activity and payouts.'
        crumbs.extend(
            [
                {'label': 'Profile', 'url': _reverse('dashboard:profile')},
                {'label': 'Notification Settings', 'url': _reverse('dashboard:notification_settings')},
            ]
        )
    elif url_name == 'event_create':
        title = 'Create a paid competition'
        subtitle = 'Set up the voting experience, timeline, pricing, and public details.'
        crumbs.extend(
            [
                {'label': 'Competitions', 'url': _reverse('dashboard:competitions')},
                {'label': 'Create', 'url': _reverse('dashboard:event_create')},
            ]
        )
        back_label = 'Back to competitions'
    elif url_name in {'event_detail', 'event_edit', 'nominee_create', 'nominee_edit', 'category_create', 'category_edit', 'nomination_queue', 'nomination_review'}:
        event_label = _event_label(event, 'Competition')
        event_url = _reverse('dashboard:event_detail', getattr(event, 'slug', '')) if event else ''
        crumbs.append({'label': 'Competitions', 'url': _reverse('dashboard:competitions')})
        crumbs.append({'label': event_label, 'url': event_url})
        title = event_label
        subtitle = 'Manage nominees, publishing, leaderboard, and voting activity.'
        if url_name == 'event_edit':
            title = 'Edit competition'
            subtitle = 'Update the public details and voting configuration.'
            crumbs.append({'label': 'Edit', 'url': _reverse('dashboard:event_edit', getattr(event, 'slug', '')) if event else ''})
            back_label = 'Back to competition'
        elif url_name == 'nominee_create':
            title = 'Add nominee'
            subtitle = 'Create a nominee profile for this competition.'
            crumbs.append({'label': 'Add nominee', 'url': _reverse('dashboard:nominee_create', getattr(event, 'slug', '')) if event else ''})
            back_label = 'Back to competition'
        elif url_name == 'nominee_edit':
            title = 'Edit nominee'
            subtitle = 'Update nominee details, photo, and voting profile.'
            nominee_label = _object_label(obj, 'Edit nominee')
            crumbs.append(
                {
                    'label': nominee_label,
                    'url': _reverse('dashboard:nominee_edit', getattr(event, 'slug', ''), getattr(obj, 'slug', '')) if event and obj else '',
                }
            )
            back_label = 'Back to competition'
        elif url_name == 'category_create':
            title = 'Add category'
            subtitle = 'Create a category nominees can be grouped and submitted under.'
            crumbs.append({'label': 'Add category', 'url': _reverse('dashboard:category_create', getattr(event, 'slug', '')) if event else ''})
            back_label = 'Back to competition'
        elif url_name == 'category_edit':
            title = 'Edit category'
            subtitle = 'Update category details and ordering.'
            category_label = _object_label(obj, 'Edit category')
            crumbs.append(
                {
                    'label': category_label,
                    'url': _reverse('dashboard:category_edit', getattr(event, 'slug', ''), getattr(obj, 'slug', '')) if event and obj else '',
                }
            )
            back_label = 'Back to competition'
        elif url_name == 'nomination_queue':
            title = 'Nomination queue'
            subtitle = 'Review pending, approved, and rejected self-nominations.'
            crumbs.append({'label': 'Nominations', 'url': _reverse('dashboard:nomination_queue', getattr(event, 'slug', '')) if event else ''})
            back_label = 'Back to competition'
        elif url_name == 'nomination_review':
            title = 'Review nomination'
            subtitle = 'Approve or reject a public nomination submission.'
            crumbs.append({'label': 'Nominations', 'url': _reverse('dashboard:nomination_queue', getattr(event, 'slug', '')) if event else ''})
            crumbs.append({'label': 'Review', 'url': _reverse('dashboard:nomination_review', getattr(event, 'slug', ''), getattr(obj, 'pk', '')) if event and obj else ''})
            back_label = 'Back to nominations'
        else:
            back_label = 'Back to competitions'
    elif url_name == 'election_create':
        title = 'Create a secure election'
        subtitle = 'Define a private election with voter eligibility, positions, and credentials.'
        crumbs.extend(
            [
                {'label': 'Elections', 'url': _reverse('dashboard:elections')},
                {'label': 'Create', 'url': _reverse('dashboard:election_create')},
            ]
        )
        back_label = 'Back to elections'
    elif url_name.startswith('election_'):
        event_label = _event_label(event, 'Election')
        event_url = _reverse('dashboard:election_detail', getattr(event, 'slug', '')) if event else ''
        crumbs.extend(
            [
                {'label': 'Elections', 'url': _reverse('dashboard:elections')},
                {'label': event_label, 'url': event_url},
            ]
        )
        title = event_label
        subtitle = 'Manage setup, voter eligibility, credentials, and tally readiness.'
        page_meta = {
            'election_edit': ('Edit election', 'Update the election timeline, description, and secure voting settings.', 'Edit'),
            'election_positions': ('Positions', 'Create the offices or categories candidates will contest.', 'Positions'),
            'election_position_edit': ('Edit position', 'Update this election position.', 'Edit position'),
            'election_candidates': ('Candidates', 'Assign candidates to positions before opening the election.', 'Candidates'),
            'election_candidate_edit': ('Edit candidate', 'Update this election candidate.', 'Edit candidate'),
            'election_roster': ('Voter roster', 'Import and verify eligible voters for this election.', 'Roster'),
            'election_invoice': ('Election invoice', 'Generate and review roster billing before issuing credentials.', 'Invoice'),
            'election_credentials': ('Voter credentials', 'Issue and export secure voter credentials.', 'Credentials'),
        }
        if url_name in page_meta:
            title, subtitle, label = page_meta[url_name]
            route_args = [getattr(event, 'slug', '')]
            if url_name in {'election_position_edit', 'election_candidate_edit'} and obj:
                route_args.append(getattr(obj, 'pk', ''))
            route = f'dashboard:{url_name}'
            crumbs.append({'label': label, 'url': _reverse(route, *route_args) if event else ''})
            back_label = 'Back to election'
        else:
            back_label = 'Back to elections'

    crumbs = _with_current(crumbs)
    back_crumb = _back_from_crumbs(crumbs)
    if back_crumb and not back_url:
        back_url = back_crumb['url']
    if back_crumb and not back_label:
        back_label = f"Back to {back_crumb['label'].lower()}"

    if url_name == 'home':
        back_label = ''
        back_url = ''

    return {
        'breadcrumbs': crumbs,
        'title': title,
        'subtitle': subtitle,
        'back_label': back_label,
        'back_url': back_url,
    }
