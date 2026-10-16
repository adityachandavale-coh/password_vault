import os
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import urlparse


def load_env(path: Path = Path('.env')) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        name, value = line.split('=', 1)
        os.environ.setdefault(name.strip(), value.strip())


load_env()
DATA_DIR = Path(os.getenv('HOMEHUB_DATA_DIR', './data')).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / 'homehub.db'
HOST = os.getenv('HOMEHUB_HOST', '127.0.0.1')
PORT = int(os.getenv('HOMEHUB_PORT', '8443'))
TIMEZONE = ZoneInfo(os.getenv('HOMEHUB_TIMEZONE', 'Asia/Kolkata'))
REMINDER_HOUR = int(os.getenv('HOMEHUB_REMINDER_HOUR', '9'))
OLLAMA_URL = os.getenv('HOMEHUB_OLLAMA_URL', 'http://127.0.0.1:11434').rstrip('/')
if urlparse(OLLAMA_URL).hostname not in {'127.0.0.1', 'localhost', 'ollama'}:
    raise ValueError('Ollama URL must point to this host or the private Docker service')
OLLAMA_MODEL = os.getenv('HOMEHUB_OLLAMA_MODEL', '')
RCLONE_FOLDER = os.getenv('HOMEHUB_RCLONE_FOLDER', 'HomeHub').strip('/\\')
REMOTES = {
    'google': os.getenv('HOMEHUB_RCLONE_GOOGLE_REMOTE', ''),
    'onedrive': os.getenv('HOMEHUB_RCLONE_ONEDRIVE_REMOTE', ''),
}
