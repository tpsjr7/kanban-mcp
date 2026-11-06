"""Open the shared project when the user visits the board's root URL."""
import hashlib
import os
from flask import redirect, request
from kanban_mcp.web import app


@app.before_request
def default_project():
    if request.path == '/' and not request.args.get('project'):
        project = hashlib.sha256(os.environ['KANBAN_PROJECT_DIR'].encode()).hexdigest()[:16]
        return redirect(f'/?project={project}')
