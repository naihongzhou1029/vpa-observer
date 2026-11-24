# VPA-View Project Instructions

## Redeploy Workflow (重新部署)

When the user asks to "重新部署" (redeploy / deploy) in this repository:
1. Restart the backend service via `./devops.sh redeploy` (or `./devops.sh restart` followed by `open http://127.0.0.1:8050`).
2. Ensure the backend process has restarted cleanly and the frontend URL (`http://127.0.0.1:8050`) is opened in a new tab of the system default browser.
