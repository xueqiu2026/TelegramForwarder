"""Read Telegram dialogs and folder rules using the already running user client."""
from datetime import datetime, timezone
from telethon import utils, types, functions


def describe_changes(previous, result):
    folders = {str(f['id']): {'title': f['title'], 'peers': sorted(f['peers'])} for f in result['folders']}
    membership = {}
    for fid, folder in folders.items():
        for peer in folder['peers']:
            membership.setdefault(peer, []).append(f'{fid}: {folder["title"]}')
    for row in result['sources']:
        old = previous['sources'].get(row['peer_id'])
        row['changes'] = []
        fields = ['telegram_title', 'username', 'member_state']
        if result['folders_complete']:
            row['folders'] = sorted(membership.get(row['peer_id'], []))
            fields.append('folders')
        if old:
            for field in fields:
                if field == 'member_state' and row[field] == 'unknown':
                    continue
                before, after = old.get(field), row.get(field)
                if before != after:
                    row['changes'].append({'field': field,
                        'before': ' / '.join(before) if isinstance(before, list) else before,
                        'after': ' / '.join(after) if isinstance(after, list) else after})
        row['change'] = ('left' if row['member_state'] == 'left' else 'added' if old is None else
                         'updated' if row['changes'] else 'unchanged')
    result['folder_changes'] = []
    if result['folders_complete']:
        for fid in sorted(previous['folders'].keys() | folders.keys()):
            old, new = previous['folders'].get(fid), folders.get(fid)
            if old != new:
                result['folder_changes'].append({'id': int(fid), 'before': old, 'after': new})


def peer_record(entity, member_state='joined'):
    peer_type = 'channel' if isinstance(entity, (types.Channel, types.ChannelForbidden)) else (
        'group' if isinstance(entity, (types.Chat, types.ChatForbidden)) else 'user')
    return {'peer_id': str(utils.get_peer_id(entity)), 'peer_type': peer_type,
            'entity_id': str(entity.id),
            'telegram_title': getattr(entity, 'title', None) or utils.get_display_name(entity),
            'username': getattr(entity, 'username', None), 'member_state': member_state}


def folder_contains(folder, dialog):
    """Return membership plus whether the Telegram folder rule was understood."""
    peer = str(utils.get_peer_id(dialog.entity))
    included = {str(utils.get_peer_id(p)) for p in [*getattr(folder, 'include_peers', []), *getattr(folder, 'pinned_peers', [])]}
    excluded = {str(utils.get_peer_id(p)) for p in getattr(folder, 'exclude_peers', [])}
    if peer in excluded:
        return False, True
    if peer in included:
        return True, True
    if isinstance(folder, types.DialogFilterChatlist):
        return False, True
    entity = dialog.entity
    if isinstance(entity, types.Channel):
        selected = bool(getattr(folder, 'groups' if entity.megagroup else 'broadcasts', False))
    elif isinstance(entity, types.Chat):
        selected = bool(getattr(folder, 'groups', False))
    else:
        selected = bool(getattr(folder, 'bots' if getattr(entity, 'bot', False) else
                                'contacts' if getattr(entity, 'contact', False) else 'non_contacts', False))
    state = dialog.dialog
    if getattr(folder, 'exclude_archived', False) and getattr(state, 'folder_id', None) == 1:
        selected = False
    if getattr(folder, 'exclude_read', False) and not (getattr(state, 'unread_count', 0) or getattr(state, 'unread_mark', False)):
        selected = False
    if getattr(folder, 'exclude_muted', False):
        mute = getattr(getattr(state, 'notify_settings', None), 'mute_until', None)
        if mute and mute > datetime.now(timezone.utc):
            selected = False
    return selected, True


async def scan_catalog(client, monitored):
    dialogs, rows, errors = [], {}, {}
    complete = True
    try:
        async for dialog in client.iter_dialogs():
            dialogs.append(dialog)
            entity = dialog.entity
            left = bool(getattr(entity, 'left', False) or getattr(entity, 'kicked', False))
            row = peer_record(entity, 'left' if left else 'joined')
            rows[row['peer_id']] = row
    except Exception as exc:
        complete = False
        errors['dialogs'] = type(exc).__name__ + ': ' + str(exc)
    # Missing dialogs alone never prove departure.
    for peer in monitored - rows.keys():
        try:
            entity = await client.get_entity(int(peer))
            left = getattr(entity, 'left', None)
            state = 'left' if left is True or getattr(entity, 'kicked', False) else 'unknown'
            rows[peer] = peer_record(entity, state)
        except Exception as exc:
            errors[peer] = type(exc).__name__ + ': ' + str(exc)
    folders = []
    try:
        result = await client(functions.messages.GetDialogFiltersRequest())
        for folder in result.filters:
            if isinstance(folder, types.DialogFilterDefault):
                continue
            title = getattr(folder.title, 'text', folder.title)
            matches = [str(utils.get_peer_id(d.entity)) for d in dialogs if folder_contains(folder, d)[0]]
            folders.append({'id': folder.id, 'title': str(title), 'complete': complete, 'peers': matches})
    except Exception as exc:
        errors['folders'] = type(exc).__name__ + ': ' + str(exc)
    return {'sources': list(rows.values()), 'folders': folders, 'complete': complete,
            'folders_complete': complete and 'folders' not in errors, 'errors': errors}
