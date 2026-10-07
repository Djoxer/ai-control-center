// Dev-server proxy for "ng serve": /api goes to the backend, so there is no CORS setup and SSE
// behaves as in production. The target comes from the environment, so this committed file never
// contains an internal IP:
//   [Environment]::SetEnvironmentVariable('ACC_DEV_BACKEND', 'http://<ai-box-ip>:8090', 'User')
// Unset or empty -> the local backend on 127.0.0.1:8090.

const DEFAULT_TARGET = 'http://127.0.0.1:8090';

/** "10.0.0.5:8090", "http://10.0.0.5:8090/" or "" -> a clean origin like "http://10.0.0.5:8090". */
export function resolveTarget(raw) {
  const value = (raw ?? '').trim();
  if (!value) return DEFAULT_TARGET;
  const withScheme = value.includes('://') ? value : `http://${value}`; // scheme is easy to forget
  let url;
  try {
    url = new URL(withScheme);
  } catch {
    throw new Error(`ACC_DEV_BACKEND is not a valid URL: "${value}" (expected e.g. http://10.0.0.5:8090)`);
  }
  if (!url.port) {
    // without a port the proxy would go to :80 and every /api call fails with a confusing 404/502
    console.warn(`[proxy] ACC_DEV_BACKEND has no port - the backend usually listens on 8090`);
  }
  return url.origin; // drops paths like ".../api" that would double the prefix
}

const target = resolveTarget(process.env.ACC_DEV_BACKEND);
console.log(`[proxy] /api -> ${target}`);

export default {
  '/api': { target, secure: false, changeOrigin: false },
};
