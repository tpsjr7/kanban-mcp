// Jest setup for jsdom environment

// Mock fetch globally
global.fetch = jest.fn();

// Helper to reset fetch mock
global.resetFetchMock = () => {
  global.fetch.mockReset();
};

// Helper to mock successful fetch response
global.mockFetchSuccess = (data, status = 200) => {
  global.fetch.mockResolvedValueOnce({
    ok: status >= 200 && status < 300,
    status: status,
    json: () => Promise.resolve(data)
  });
};

// Helper to mock failed fetch response
global.mockFetchError = (error) => {
  global.fetch.mockRejectedValueOnce(new Error(error));
};

// The browser template loads this helper before timeline.js.
require('../kanban_mcp/static/browser-time.js');
require('../kanban_mcp/static/update-attribution.js');

// The server template supplies per-project columns before loading dragdrop.js.
const builtinPermissions = {
  issue: ['backlog', 'todo', 'in_progress', 'review', 'done', 'closed'],
  feature: ['backlog', 'todo', 'in_progress', 'review', 'done', 'closed'],
  epic: ['backlog', 'todo', 'in_progress', 'review', 'done', 'closed'],
  todo: ['backlog', 'todo', 'in_progress', 'done'],
  diary: ['done'],
  question: ['backlog', 'review', 'closed'],
};
window.kanbanColumns = {
  revision: 0,
  columns: ['backlog', 'todo', 'in_progress', 'review', 'done', 'closed'].map(key => ({
    key,
    label: key,
    allowed_types: Object.keys(builtinPermissions).filter(type => builtinPermissions[type].includes(key)),
  })),
};
