/* Project column editing. Drafts stay local until an atomic Save. */
(() => {
    const types = ['issue', 'todo', 'feature', 'diary', 'epic', 'question'];
    const config = window.kanbanColumns;
    const project = new URLSearchParams(location.search).get('project');
    function element(tag, text) {
        const node = document.createElement(tag);
        if (text !== undefined) node.textContent = text;
        return node;
    }
    // Replace hardcoded edit/export choices with stable keys and current labels.
    for (const id of ['edit-status', 'export-status']) {
        const select = document.getElementById(id);
        if (!select) continue;
        select.replaceChildren();
        if (id === 'export-status') select.append(new Option('All', ''));
        for (const column of config.columns) select.append(new Option(column.label, column.key));
    }
    document.getElementById('manage-columns')?.addEventListener('click', () => {
        let draft = structuredClone(config.columns);
        const destinations = {};
        const dialog = element('dialog');
        dialog.className = 'columns-dialog';
        dialog.append(element('h2', 'Columns'));
        const list = element('div');
        const error = element('p');
        error.setAttribute('role', 'alert');
        function button(text, action) {
            const node = element('button', text);
            node.type = 'button';
            node.addEventListener('click', action);
            return node;
        }
        function move(from, to) {
            if (to < 0 || to >= draft.length) return;
            draft.splice(to, 0, draft.splice(from, 1)[0]);
            render();
        }
        let dragging;
        function render() {
            list.replaceChildren();
            draft.forEach((column, index) => {
                const row = element('section');
                row.className = 'columns-row';
                row.addEventListener('dragover', event => event.preventDefault());
                row.addEventListener('drop', event => {
                    event.preventDefault();
                    if (Number.isInteger(dragging)) move(dragging, index);
                    dragging = undefined;
                });
                const header = element('div');
                header.className = 'columns-row-header';
                const grip = button('↕', () => {});
                grip.draggable = true;
                grip.setAttribute('aria-label', 'Drag ' + column.label);
                grip.addEventListener('dragstart', () => { dragging = index; });
                const name = element('input');
                name.value = column.label;
                name.maxLength = 80;
                name.disabled = Boolean(column.builtin);
                name.setAttribute('aria-label', 'Column name');
                name.addEventListener('input', () => { column.label = name.value; });
                const up = button('↑', () => move(index, index - 1));
                up.disabled = index === 0;
                up.setAttribute('aria-label', 'Move ' + column.label + ' up');
                const down = button('↓', () => move(index, index + 1));
                down.disabled = index === draft.length - 1;
                down.setAttribute('aria-label', 'Move ' + column.label + ' down');
                header.append(grip, name, up, down);
                row.append(header);
                const choices = element('div');
                choices.className = 'columns-types';
                for (const type of types) {
                    const label = element('label');
                    const check = element('input');
                    check.type = 'checkbox';
                    check.checked = column.allowed_types.includes(type);
                    check.addEventListener('change', () => {
                        column.allowed_types = check.checked
                            ? [...column.allowed_types, type]
                            : column.allowed_types.filter(value => value !== type);
                    });
                    label.append(check, document.createTextNode(type));
                    choices.append(label);
                }
                row.append(choices);
                if (!column.builtin) row.append(button('Delete', () => {
                    const target = element('select');
                    target.setAttribute('aria-label', 'Move cards to');
                    target.append(new Option('Move cards to…', ''));
                    for (const other of draft) {
                        if (other !== column && other.status_id) target.append(new Option(other.label, other.status_id));
                    }
                    const confirm = button('Confirm deletion', () => {
                        if (target.value) {
                            destinations[String(column.status_id)] = Number(target.value);
                        }
                        draft = draft.filter(value => value !== column);
                        render();
                    });
                    row.append(target, confirm);
                }));
                list.append(row);
            });
        }
        render();
        const actions = element('div');
        const save = button('Save', async () => {
            save.disabled = true;
            error.textContent = '';
            try {
                const response = await fetch(`/api/projects/${encodeURIComponent(project)}/columns`, {
                    method: 'PUT', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({revision: config.revision, columns: draft, destinations})
                });
                const result = await response.json();
                if (!response.ok) throw new Error(result.error || 'Unable to save columns');
                location.reload();
            } catch (failure) {
                error.textContent = failure.message;
                save.disabled = false;
            }
        });
        actions.append(button('Add column', () => {
            draft.push({label: '', allowed_types: [...types], builtin: false});
            render();
        }), button('Cancel', () => dialog.close()), save);
        dialog.append(list, error, actions);
        dialog.addEventListener('close', () => dialog.remove());
        document.body.append(dialog);
        dialog.showModal();
    });
})();
