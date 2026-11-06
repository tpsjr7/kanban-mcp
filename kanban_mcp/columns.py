"""Project column configuration shared by HTTP and ordinary MCP clients."""
import uuid


def read(db, project):
    with db._db_cursor(dictionary=True) as cursor:
        cursor.execute('SELECT revision FROM column_revisions WHERE project_id=?', (project,))
        row = cursor.fetchone()
        if not row:
            raise ValueError('Unknown project')
        revision = row['revision']
        cursor.execute('''SELECT c.*,s.name AS key FROM project_columns c
            JOIN statuses s ON s.id=c.status_id WHERE project_id=? ORDER BY position''', (project,))
        columns = cursor.fetchall()
        for column in columns:
            cursor.execute('''SELECT t.name FROM column_types c JOIN item_types t ON t.id=c.type_id
                WHERE project_id=? AND status_id=? ORDER BY t.id''', (project, column['status_id']))
            column['allowed_types'] = [r['name'] for r in cursor.fetchall()]
            column['completed'] = column['key'] in ('done', 'closed')
    return {'revision': revision, 'columns': columns}


def save(db, project, payload):
    """Apply a complete panel draft atomically, including explicit deletion moves."""
    columns = payload['columns']
    if not isinstance(columns, list) or not columns:
        raise ValueError('At least one column is required')
    with db._db_cursor(dictionary=True, commit=True) as cursor:
        cursor.execute('BEGIN IMMEDIATE')
        cursor.execute('SELECT revision FROM column_revisions WHERE project_id=?', (project,))
        row = cursor.fetchone()
        if not row or row['revision'] != payload.get('revision'):
            raise ValueError('Columns changed; reload settings before saving')
        cursor.execute('SELECT * FROM project_columns WHERE project_id=?', (project,))
        old = {r['status_id']: r for r in cursor.fetchall()}
        cursor.execute('SELECT id,name FROM item_types')
        types = {r['name']: r['id'] for r in cursor.fetchall()}
        seen, labels, covered = set(), set(), set()
        prepared = []
        for column in columns:
            label = column['label'].strip()
            allowed = column['allowed_types']
            if not label or len(label) > 80 or label.casefold() in labels:
                raise ValueError('Column names must be unique and contain 1–80 characters')
            if not allowed or len(set(allowed)) != len(allowed) or any(t not in types for t in allowed):
                raise ValueError('Choose valid card types for every column')
            labels.add(label.casefold())
            covered.update(allowed)
            status = column.get('status_id')
            if status is not None:
                if status not in old or status in seen:
                    raise ValueError('Invalid or duplicate column')
                if old[status]['builtin'] and label != old[status]['label']:
                    raise ValueError('Built-in columns cannot be renamed')
                seen.add(status)
            prepared.append((status, label, allowed))
        if covered != set(types):
            raise ValueError('Every card type needs an available column')
        removed = set(old) - seen
        if any(old[s]['builtin'] for s in removed):
            raise ValueError('Built-in columns cannot be deleted')
        # Destination restrictions are checked against the final draft.
        rules = {s: a for s, _, a in prepared if s is not None}
        completed_moves = []
        for status in removed:
            cursor.execute('''SELECT i.id,t.name FROM items i JOIN item_types t ON t.id=i.type_id
                WHERE project_id=? AND status_id=?''', (project, status))
            cards = cursor.fetchall()
            destination = payload.get('destinations', {}).get(str(status))
            if cards and destination not in rules:
                raise ValueError('Choose a destination for occupied deleted columns')
            for card in cards:
                if card['name'] not in rules[destination]:
                    raise ValueError(f"Destination excludes card #{card['id']}")
                cursor.execute('SELECT name FROM statuses WHERE id=?', (destination,))
                target = cursor.fetchone()['name']
                if db._check_blocking_constraint(card['id'], target):
                    raise ValueError(f"Card #{card['id']} is blocked")
            # Install final destination rules before moving cards.
            if cards:
                cursor.execute('DELETE FROM column_types WHERE project_id=? AND status_id=?', (project, destination))
                for name in rules[destination]:
                    cursor.execute('INSERT INTO column_types VALUES(?,?,?)', (project, destination, types[name]))
            for card in cards:
                cursor.execute('''UPDATE items SET status_id=?,closed_at=CASE WHEN ? IN ('done','closed')
                    THEN datetime('now') ELSE NULL END WHERE id=?''', (destination, target, card['id']))
                cursor.execute('''INSERT INTO status_history(item_id,old_status_id,new_status_id,change_type)
                    VALUES(?,?,?,'set')''', (card['id'], status, destination))
            if cards and target in ('done', 'closed'):
                completed_moves.extend(card['id'] for card in cards)
            cursor.execute('DELETE FROM column_types WHERE project_id=? AND status_id=?', (project, status))
            cursor.execute('DELETE FROM project_columns WHERE project_id=? AND status_id=?', (project, status))
        # Temporary labels allow atomic swaps without UNIQUE collisions.
        cursor.execute("UPDATE project_columns SET label='__draft_' || status_id WHERE project_id=?", (project,))
        for position, (status, label, allowed) in enumerate(prepared):
            if status is None:
                cursor.execute('INSERT INTO statuses(name) VALUES(?)', ('custom_' + uuid.uuid4().hex,))
                status = cursor.lastrowid
                cursor.execute('INSERT INTO project_columns VALUES(?,?,?,?,0)', (project, status, label, position))
            placeholders = ','.join('?' for _ in allowed)
            cursor.execute(
                'SELECT id FROM items WHERE project_id=? AND status_id=? '
                'AND type_id NOT IN (' + placeholders + ')',
                (project, status, *(types[t] for t in allowed)))
            excluded = cursor.fetchall()
            if excluded:
                raise ValueError('Move these cards first: ' + ', '.join('#' + str(r['id']) for r in excluded))
            cursor.execute(
                'UPDATE project_columns SET label=?,position=? '
                'WHERE project_id=? AND status_id=?',
                (label, position, project, status))
            cursor.execute('DELETE FROM column_types WHERE project_id=? AND status_id=?', (project, status))
            for name in allowed:
                cursor.execute('INSERT INTO column_types VALUES(?,?,?)', (project, status, types[name]))
        # Preserve ancestor completion in the same transaction as deletion moves.
        for card_id in completed_moves:
            card = db.get_item(card_id)
            while card and card.get('parent_id'):
                parent = db.get_item(card['parent_id'])
                if not parent or parent['type_name'] != 'epic':
                    break
                progress = db.get_epic_progress(parent['id'])
                cursor.execute(
                    'SELECT 1 FROM column_types WHERE project_id=? AND status_id=4 AND type_id=5',
                    (project,))
                permitted = cursor.fetchone()
                if (permitted and progress['total'] > 0
                        and progress['completed'] == progress['total']
                        and parent['status_name'] not in ('review', 'done', 'closed')):
                    cursor.execute('UPDATE items SET status_id=4 WHERE id=?', (parent['id'],))
                    cursor.execute(
                        'INSERT INTO status_history(item_id,old_status_id,new_status_id,change_type) '
                        "VALUES(?,?,4,'auto_advance')", (parent['id'], parent['status_id']))
                card = parent
        cursor.execute('UPDATE column_revisions SET revision=revision+1 WHERE project_id=?', (project,))
    return read(db, project)


def install(cls):
    original_create = cls.create_item

    def create(self, project_id, type_name, title, description=None, priority=3,
               complexity=None, status_name=None, parent_id=None):
        available = [c for c in read(self, project_id)['columns'] if type_name in c['allowed_types']]
        if not status_name:
            status_name = available[0]['key']
        return original_create(self, project_id, type_name, title, description,
                               priority, complexity, status_name, parent_id)

    def set_status(self, item_id, status_name):
        item = self.get_item(item_id)
        if not item:
            raise ValueError('Item not found')
        column = next((c for c in read(self, item['project_id'])['columns']
                       if c['key'] == status_name and item['type_name'] in c['allowed_types']), None)
        if not column:
            raise ValueError('Column does not allow this card type in this project')
        from kanban_mcp.team_runtime import effectively_complete
        completed = effectively_complete(self, item_id, status_name)
        blocked = self._check_blocking_constraint(item_id, status_name)
        if blocked:
            return blocked
        with self._db_cursor(commit=True) as cursor:
            cursor.execute(
                "UPDATE items SET status_id=?,closed_at=CASE WHEN ? "
                "THEN datetime('now') ELSE NULL END WHERE id=?",
                           (column['status_id'], completed, item_id))
        self._record_status_change(item_id, item['status_id'], column['status_id'], 'set')
        if completed:
            self._auto_advance_ancestors(item_id)
        return {'success': True, 'previous_status': item['status_name'], 'new_status': status_name}

    def step(self, item_id, direction):
        item = self.get_item(item_id)
        columns = [c for c in read(self, item['project_id'])['columns'] if item['type_name'] in c['allowed_types']]
        index = next(i for i, c in enumerate(columns) if c['status_id'] == item['status_id']) + direction
        if index < 0 or index >= len(columns):
            return {'success': False, 'message': 'Already at workflow boundary'}
        return set_status(self, item_id, columns[index]['key'])

    def close(self, item_id):
        item = self.get_item(item_id)
        columns = [c for c in read(self, item['project_id'])['columns']
                   if c['completed'] and item['type_name'] in c['allowed_types']]
        if not columns:
            raise ValueError('No permitted completion column')
        target = next((c for c in columns if c['key'] == 'closed'), columns[0])
        return set_status(self, item_id, target['key'])

    cls.create_item = create
    cls.set_status = set_status
    cls.advance_status = lambda self, item_id: step(self, item_id, 1)
    cls.revert_status = lambda self, item_id: step(self, item_id, -1)
    cls.close_item = close
