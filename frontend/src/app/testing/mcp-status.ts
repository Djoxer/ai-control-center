import { McpServerStatus } from '../api/models/mcp-server-status';

/** A stopped demo server as the backend reports it; override what a test is about. */
export const mcpStatus = (over: Partial<McpServerStatus> = {}): McpServerStatus => ({
  revision: 1, asOf: '2026-10-07T12:00:00Z', key: 'demo', title: 'Demo', url: 'http://127.0.0.1:8701/mcp',
  commandLine: 'python -m demo', cwd: '/srv', autostart: false, restartOnCrash: true, state: 'stopped',
  logSource: 'mcp-demo', port: { port: 8701, open: false, managed: false }, ...over,
});
