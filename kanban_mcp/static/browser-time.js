// SQLite stores UTC timestamps without an offset. Explicit offsets stay intact.
(() => {
    function parse(value) {
        if (typeof value !== 'string') return new Date(value);
        const timestamp = value.trim();
        const naive = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?$/;
        return new Date(naive.test(timestamp)
            ? timestamp.replace(' ', 'T') + 'Z'
            : timestamp);
    }

    function dayKey(value) {
        const date = parse(value);
        const month = String(date.getMonth() + 1).padStart(2, '0');
        const day = String(date.getDate()).padStart(2, '0');
        return `${date.getFullYear()}-${month}-${day}`;
    }

    function dayLabel(key, now = new Date()) {
        if (key === dayKey(now)) return 'Today';
        const yesterday = new Date(now);
        yesterday.setDate(yesterday.getDate() - 1);
        if (key === dayKey(yesterday)) return 'Yesterday';
        // A date-group key is already local; do not parse it as UTC midnight.
        const [year, month, day] = key.split('-').map(Number);
        return new Date(year, month - 1, day).toLocaleDateString('en-US', {
            weekday: 'short', month: 'short', day: 'numeric',
        });
    }

    globalThis.KanbanTime = Object.freeze({ parse, dayKey, dayLabel });
})();
