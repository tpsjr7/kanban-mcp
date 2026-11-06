/* Managed ticket details use text nodes; plan snapshots never serve host files. */
window.KanbanWorkflow = {
    async load(itemId) {
        const panel = document.getElementById('workflow-details');
        panel.replaceChildren();
        try {
            const project = new URLSearchParams(location.search).get('project');
            const response = await fetch(`/api/projects/${encodeURIComponent(project)}/workflow`);
            if (!response.ok) throw new Error('Unable to load workflow');
            const data = await response.json();
            const run = data.tickets.find(ticket => ticket.item_id === itemId);
            if (!run) return;
            const label = document.createElement('p');
            const worker = data.team_workers.find(worker => worker.id === run.worker);
            const pending = data.reports?.some(report => report.item_id === itemId && report.state === 'pending');
            const paused = data.orchestration_settings[0]?.paused;
            const phase = window.kanbanColumns.columns.find(column => column.key === run.phase)?.label
                || run.phase.replaceAll('_', ' ');
            label.textContent = [run.team, worker?.title, phase,
                paused ? 'Paused' : pending ? 'Pending verification' : null].filter(Boolean).join(' · ');
            label.style.marginBottom = '8px';
            panel.style.marginBottom = '16px';
            panel.append(label);
            if (run.reason) {
                const reason = document.createElement('p');
                reason.textContent = run.reason;
                reason.setAttribute('role', 'status');
                panel.append(reason);
            }
            if (run.plan || run.plan_path || run.plan_chat_id) {
                const plan = document.createElement('div');
                plan.style.display = 'grid';
                plan.style.gap = '8px';
                plan.style.marginBottom = '16px';
                const heading = document.createElement('span');
                heading.textContent = 'Plan';
                plan.append(heading);
                const path = run.plan_path || run.plan;
                if (path) {
                    const filename = document.createElement('code');
                    filename.textContent = path;
                    filename.style.overflowWrap = 'anywhere';
                    plan.append(filename);
                }
                if (run.plan_chat_id && run.plan_client === 'codex') {
                    const chat = document.createElement('a');
                    chat.href = `codex://threads/${encodeURIComponent(run.plan_chat_id)}`;
                    chat.textContent = run.plan_chat_title || 'Open in Codex';
                    chat.style.color = 'var(--md-sys-color-primary)';
                    plan.append(chat);
                } else if (run.plan_chat_id && window.KanbanAttribution) {
                    plan.append(window.KanbanAttribution.element({
                        source_client: run.plan_client,
                        chat_id: run.plan_chat_id,
                        agent_name: run.plan_chat_title || 'OpenCode',
                        chat_title: run.plan_chat_title,
                    }));
                }
                panel.append(plan);
            }
            if (run.preview) {
                const url = new URL(run.preview);
                if (url.protocol === 'https:' && url.hostname.endsWith('.flippyface.com')) {
                    const preview = document.createElement('a');
                    preview.href = url.href;
                    preview.target = '_blank';
                    preview.rel = 'noopener';
                    preview.textContent = 'Preview';
                    panel.append(preview);
                }
            }
        } catch (error) { panel.textContent = error.message; }
    }
};
