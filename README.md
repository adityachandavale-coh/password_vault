# HomeHub

HomeHub is a private, single-user home app for documents, expiry reminders, expenses, and an encrypted password vault. One computer runs the service. Browsers and phones on the same Wi‑Fi connect to that computer over HTTPS. Prompts and document extraction use a locally running Ollama model when configured.

## What is implemented

- One-account sign-in protected by a setup code read on the host.
- Client-encrypted password records, a generator, and local password-health counts. The master password stays on the client and cannot be recovered by the server.
- Document and bill uploads, encrypted local copies, optional sync to **both** Google Drive and OneDrive through separately configured rclone remotes, a browser for each app folder, and local Ollama extraction of candidate dates and bill fields. Confirming an expiry can create a reminder.
- Expense capture with an optional bill in the same form; category, merchant, date, payment method, and spending reports.
- Manual reminders, calendar `.ics` downloads that open in device calendars, and SMTP email delivery from the always-on host.
- A unified prompt that creates a reviewable draft and fills the matching manual tab. The model does not write records directly.
- Responsive browser interface and installable web app for phone home screens.

## Limits of this build

This is an initial personal app, **not a security-audited 1Password replacement**. The vault has no browser autofill, passkeys, sharing, or recovery service. Calendar integration uses an `.ics` event import; it does not read or continuously sync a device calendar. Native iOS and Android packaging has not been produced. The Capacitor configuration is present for later packaging, but the package toolchain could not be installed in this workspace. iOS native builds require a Mac. The app works as a mobile web app in the meantime.

The host must be running for other devices to access the app and for emails to send. Google Drive, OneDrive, and SMTP require internet. Documents and bills copied to those providers are readable under the provider account's usual permissions. Keep the host, backups, and rclone configuration protected.

## Deploy on another Windows machine

1. Copy this project to the host. Install Python 3.12+, Ollama, and rclone on that host. Give it a stable LAN address (a router DHCP reservation is useful).
2. In PowerShell, run `python -m venv .venv` and `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`.
3. Copy `.env.example` to `.env`. Set `HOMEHUB_OLLAMA_MODEL`, SMTP settings, the two rclone remote names, and the reminder time zone/hour. Do not put real credentials into source control.
4. Run `rclone config` on the host and create one Google Drive remote and one OneDrive remote. Set their names in `.env`. For Google Drive, use your own OAuth client ID; rclone says its shared client ID is being retired in 2026. Use `rclone lsd remote-name:` to verify each remote.
5. Pull a local text and vision capable Ollama model suitable for the host hardware with `ollama pull MODEL_NAME`; set that name in `.env`. Keep Ollama bound to localhost.
6. Create a certificate for the address other devices will use: `.\.venv\Scripts\python.exe scripts\make_cert.py --host 192.168.1.50` (replace the IP). Keep `data\certs\ca.key` private. Install and trust `data\certs\ca.crt` on every device that will open HomeHub.
7. Start HomeHub with `powershell -File scripts\start.ps1`. Permit **port 8443 only on the private LAN** in the host firewall. Do not forward it on the router.
8. Read `data\setup.token` on the host. Open `https://HOST-IP:8443` and enter that code to create the first account. The setup code is required only once.

To run automatically, configure the host's service manager or Task Scheduler to start `scripts/start.ps1` after login/startup, and disable sleep while reminders are needed.

## Docker deployment on another machine

1. Install Docker Compose. Copy the project and create `.env` from `.env.example`.
2. `docker compose build`
3. `docker compose run --rm --no-deps app python scripts/make_cert.py --host HOST-IP`
4. `docker compose up -d`
5. `docker compose exec ollama ollama pull MODEL_NAME` and set `HOMEHUB_OLLAMA_MODEL=MODEL_NAME` in `.env`; restart the app service.
6. Configure each rclone remote with `docker compose run --rm --no-deps app rclone config`. Set both remote names in `.env`; restart the app service. The rclone configuration persists in `./rclone`.
7. Read the setup code with `docker compose exec app cat /app/data/setup.token`. Trust `./data/certs/ca.crt` on client devices, then open `https://HOST-IP:8443`.

Only the app's HTTPS port is published. Ollama is reachable only inside Docker Compose. The server and encrypted local documents live in `./data`; cloud OAuth tokens live in `./rclone`. Back up both directories securely. Losing `data/host.key` makes locally stored documents unreadable. Losing your vault master password makes vault records unreadable.

## Backup and restore

On a direct Windows installation, run `.\.venv\Scripts\python.exe scripts\backup.py create homehub-backup.hhb`. In Docker, run `docker compose run --rm --no-deps app python scripts/backup.py create data/homehub-backup.hhb` and copy the archive out of `./data`. It prompts for a backup passphrase and creates an encrypted archive containing a consistent database snapshot, local encrypted documents, the host key, certificates, and rclone configuration. Keep that archive and passphrase separately. On a new host with empty `data` and `rclone` directories, run the corresponding `restore` command before starting the app. The vault master password is still required after restore. Stop the app before restoring.

## Development and tests

After installing Python dependencies, run `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` on Windows. For local UI development, run `.\.venv\Scripts\python.exe -m uvicorn homehub.main:app --host 127.0.0.1 --port 8765` and open `http://127.0.0.1:8765`. Use HTTPS for any LAN access or real vault data.

## Configuration

See `.env.example`. `HOMEHUB_OLLAMA_MODEL` is deliberately unselected until the deployment machine's RAM and GPU are known. Without it, basic prompt parsing works, but image date extraction does not. SMTP credentials remain on the host. No prompt text or vault secret is sent to an external AI provider.
