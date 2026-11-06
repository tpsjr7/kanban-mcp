// Attribution is displayed as text; chat IDs are never interpreted as HTML or URLs.
(() => {
    function element(update) {
        const identity = update.attribution || update;
        const root = document.createElement('span');
        root.className = 'update-attribution';
        const name = document.createElement('span');
        name.className = 'update-author';
        name.textContent = identity.browser_username || identity.agent_name || 'Unknown author';
        root.appendChild(name);
        if (!identity.chat_id) return root;

        const id = String(identity.chat_id);
        const details = document.createElement('details');
        details.className = 'update-chat';
        const summary = document.createElement('summary');
        summary.textContent = 'Chat';
        summary.title = identity.chat_title ? `${identity.chat_title} · ${id}` : id;
        details.appendChild(summary);
        const panel = document.createElement('span');
        panel.className = 'update-chat-details';
        const code = document.createElement('code');
        code.textContent = id;
        panel.appendChild(code);
        const copy = document.createElement('button');
        copy.type = 'button';
        copy.className = 'btn btn-secondary btn-sm';
        copy.textContent = 'Copy ID';
        copy.addEventListener('click', async () => {
            try {
                await navigator.clipboard.writeText(id);
                copy.textContent = 'Copied';
            } catch {
                copy.textContent = 'Select ID to copy';
            }
        });
        panel.appendChild(copy);
        if (identity.source_client === 'codex') {
            const link = document.createElement('a');
            link.textContent = 'Open in Codex';
            link.href = `codex://threads/${encodeURIComponent(id)}`;
            panel.appendChild(link);
        }
        details.appendChild(panel);
        root.appendChild(details);
        return root;
    }

    globalThis.KanbanAttribution = Object.freeze({ element });
})();
