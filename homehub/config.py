import os
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path | None = None) -> None:
    path = path or PROJECT_ROOT / '.env'
    if not path.exists():
        return
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        name, value = line.split('=', 1)
        value = value.strip()
        if len(value) > 1 and value[0] == value[-1] and value[0] in '"\'':
            value = value[1:-1]
        os.environ.setdefault(name.strip(), value)


def resolve_path(value: str) -> Path:
    """Resolve a configured path against the project root, not the process CWD.

    The service is started from a shell, Task Scheduler, systemd, launchd, and
    Docker, each with its own working directory. Anchoring to the project root
    keeps one data directory across all of them.
    """
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (PROJECT_ROOT / path).resolve()


load_env()
DATA_DIR = resolve_path(os.getenv('HOMEHUB_DATA_DIR', './data'))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / 'homehub.db'
CERT_DIR = resolve_path(os.getenv('HOMEHUB_CERT_DIR', str(DATA_DIR / 'certs')))
HOST = os.getenv('HOMEHUB_HOST', '127.0.0.1')
PORT = int(os.getenv('HOMEHUB_PORT', '8443'))
TIMEZONE = ZoneInfo(os.getenv('HOMEHUB_TIMEZONE', 'Asia/Kolkata'))
REMINDER_HOUR = int(os.getenv('HOMEHUB_REMINDER_HOUR', '9'))
if not 0 <= REMINDER_HOUR <= 23:
    raise ValueError('HOMEHUB_REMINDER_HOUR must be between 0 and 23')
OLLAMA_URL = os.getenv('HOMEHUB_OLLAMA_URL', 'http://127.0.0.1:11434').rstrip('/')
if urlparse(OLLAMA_URL).hostname not in {'127.0.0.1', '::1', 'localhost', 'ollama'}:
    raise ValueError('Ollama URL must point to this host or the private Docker service')
OLLAMA_MODEL = os.getenv('HOMEHUB_OLLAMA_MODEL', '')
RCLONE_FOLDER = os.getenv('HOMEHUB_RCLONE_FOLDER', 'HomeHub').strip('/\\')
REMOTES = {
    'google': os.getenv('HOMEHUB_RCLONE_GOOGLE_REMOTE', ''),
    'onedrive': os.getenv('HOMEHUB_RCLONE_ONEDRIVE_REMOTE', ''),
}
