"""JSON stdin control for a network-isolated helper container."""
import json
import os
import sys
from kanban_mcp.core import KanbanDB
from kanban_mcp import team_runtime as runtime


_database = None

def main(request):
    global _database
    if _database is None:
        _database = KanbanDB()
    db = _database
    project = db.hash_project_path(os.environ.get('KANBAN_PROJECT_DIR', '/workspace/project'))
    action, data = request['action'], request.get('data', {})
    if action == 'setup':
        runtime.setup(db, project)
        return runtime.snapshot(db, project)
    if action == 'snapshot':
        return runtime.snapshot(db, project)
    if action == 'poll':
        return runtime.consume(db, project)
    if action == 'reports':
        with db._db_cursor(dictionary=True) as c:
            c.execute("SELECT p.*,d.item_id,d.kind AS dispatch_kind,d.revision,d.worker AS worker_id,d.created_at AS dispatched_at "
                      "FROM workflow_reports p JOIN workflow_dispatches d ON d.id=p.dispatch_id WHERE p.state='pending'")
            return c.fetchall()
    if action.startswith('host_'):
        return runtime.host_action(db, action[5:], data)
    return runtime.configure(db, project, action, data)


if __name__ == '__main__':
    if '--serve' in sys.argv:
        for line in sys.stdin:
            try:
                print(json.dumps({'result': main(json.loads(line))}, default=str), flush=True)
            except Exception as error:
                print(json.dumps({'error': str(error)}), flush=True)
    else:
        try:
            print(json.dumps(main(json.load(sys.stdin)), default=str))
        except Exception as error:
            print(json.dumps({'error': str(error)}))
            sys.exit(1)
