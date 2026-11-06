"""Per-update attribution; no credentials or connection-wide identity state."""
FIELDS = ('source_client', 'browser_username', 'chat_id', 'chat_title', 'agent_name')


def clean(value, field, limit=512):
    if value is None:
        return ''
    if not isinstance(value, str):
        raise ValueError(f'{field} must be a string')
    value = value.strip()
    if len(value) > limit or any(ord(char) < 32 for char in value):
        raise ValueError(f'{field} must be at most {limit} characters without control characters')
    return value


def agent_identity(client, chat_id, agent_name='', chat_title=''):
    client = clean(client, 'client').lower()
    chat_id = clean(chat_id, 'chat_id')
    name = clean(agent_name, 'agent_name')
    title = clean(chat_title, 'chat_title')
    if client not in ('codex', 'opencode'):
        raise ValueError('client must be codex or opencode')
    if not chat_id or not (name or title):
        raise ValueError('Supply the exact chat_id and agent_name or current chat_title; do not guess')
    return dict(source_client=client, browser_username=None, chat_id=chat_id,
                chat_title=title or None, agent_name=name or title)


def browser_identity(authorization):
    if authorization is None or authorization.type.lower() != 'basic':
        raise ValueError('Browser updates require a Basic Auth login')
    username = clean(authorization.username, 'browser username')
    if not username:
        raise ValueError('Browser updates require a Basic Auth username')
    return dict(source_client='browser', browser_username=username,
                chat_id=None, chat_title=None, agent_name=None)


def update_fields(row):
    return {field: row.get(field) for field in FIELDS}


def author(row):
    return row.get('browser_username') or row.get('agent_name') or 'Unknown author'


def store_update(database, project_id, content, item_ids=None, attribution=None):
    identity = update_fields(attribution or {})
    # Update, identity, and ticket links commit together, or all roll back.
    with database._db_cursor(commit=True) as cursor:
        cursor.execute(database._sql('''
            INSERT INTO updates
              (project_id, content, source_client, browser_username,
               chat_id, chat_title, agent_name)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        '''), (project_id, content, *(identity[field] for field in FIELDS)))
        update_id = cursor.lastrowid
        for item_id in item_ids or []:
            cursor.execute(database._sql('''
                INSERT INTO update_items (update_id, item_id) VALUES (%s, %s)
            '''), (update_id, item_id))
    database._safe_embedding_op('upsert_embedding', 'update', update_id)
    return update_id


def ensure_schema(database):
    with database._db_cursor(dictionary=True) as cursor:
        cursor.execute('PRAGMA table_info(updates)')
        columns = {column['name'] for column in cursor.fetchall()}
        cursor.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name=?",
                       ('require_update_attribution',))
        trigger = cursor.fetchone()
    if not set(FIELDS).issubset(columns) or not trigger:
        raise RuntimeError('Update attribution migration is incomplete; inspect migration logs before starting')
