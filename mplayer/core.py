"""System inspection and deterministic, conservative scaling policy."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess

PROFILES = {
    "Экономичный": {"scale": "bilinear", "cscale": "bilinear", "dscale": "bilinear", "deband": False},
    "Сбалансированный": {"scale": "lanczos", "cscale": "bilinear", "dscale": "mitchell", "deband": False},
    "Качественный": {"scale": "ewa_lanczossharp", "cscale": "lanczos", "dscale": "mitchell", "deband": True},
}

AUDIO_EXTENSIONS = {
    '.aac', '.ac3', '.aiff', '.aif', '.alac', '.ape', '.dts', '.dtshd',
    '.eac3', '.ec3', '.flac', '.m4a', '.mka', '.mp2', '.mp3', '.oga',
    '.ogg', '.opus', '.thd', '.truehd', '.wav', '.wma', '.wv',
}


def audio_files(directory):
    """List audio files directly in a folder; an empty folder setting disables it."""
    if not directory.strip():
        return []
    path = Path(directory.strip()).expanduser().resolve()
    return [str(p) for p in sorted(path.iterdir(), key=lambda p: p.name.casefold())
            if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS]


def audio_folder_commands(files, tracks, managed):
    """Replace only tracks previously added by our folder picker."""
    wanted = set(files)
    external = [t for t in tracks if t.get('type') == 'audio' and t.get('external')]
    present = {t.get('external-filename') for t in external}
    commands = [['audio-remove', t['id']] for t in external
                if t.get('external-filename') in managed - wanted]
    commands.extend(['audio-add', f, 'auto', Path(f).name]
                    for f in files if f not in present)
    # Do not claim tracks which were already loaded by mpv or the user.
    return commands, (managed & wanted) | (wanted - present)

@dataclass
class SystemInfo:
    system: str
    cpu_threads: int
    ram_gib: float
    gpu: str
    mpv: str | None
    session: str

def inspect_system():
    ram = 0.0
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith('MemTotal:'):
                ram = round(int(line.split()[1]) / 1024**2, 1)
    except (OSError, ValueError):
        pass
    gpu = 'Не определена'
    if shutil.which('lspci'):
        try:
            result = subprocess.run(['lspci'], capture_output=True, text=True, timeout=4)
            cards = [s for s in result.stdout.splitlines() if any(k in s.lower() for k in ('vga compatible', '3d controller', 'display controller'))]
            gpu = '\n'.join(cards) or gpu
        except (OSError, subprocess.TimeoutExpired):
            pass
    return SystemInfo(platform.system(), os.cpu_count() or 1, ram, gpu,
                      shutil.which('mpv'), os.environ.get('XDG_SESSION_TYPE', 'unknown'))

def choose_profile(info, width=0, height=0, fps=0, target_width=1920, target_height=1080):
    # GPU names do not reliably indicate throughput, especially on hybrid laptops.
    if info.gpu == 'Не определена' or (info.ram_gib and info.ram_gib < 4):
        return 'Экономичный', 'Недостаточно данных о GPU или мало памяти; безопасный профиль.'
    if max(width * height, target_width * target_height) > 2560 * 1440 or fps > 50:
        return 'Экономичный', 'Высокое разрешение или FPS: уменьшаем нагрузку.'
    return 'Сбалансированный', 'Консервативный старт; пропуски кадров снизят качество автоматически.'

class DropGuard:
    def __init__(self):
        self.reset()

    def reset(self):
        self.last = None
        self.bad = 0
        self.warmup = 4

    def sample(self, count):
        if count is None:
            return False
        count = int(count)
        delta = max(0, count - self.last) if self.last is not None else 0
        self.last = count
        if self.warmup:
            self.warmup -= 1
            return False
        self.bad = self.bad + 1 if delta >= 3 else 0
        if self.bad >= 3:
            self.bad = 0
            return True
        return False

def settings_path():
    return Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'mplayer-adaptive' / 'settings.json'

def load_settings():
    try:
        data = json.loads(settings_path().read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}

def save_settings(data):
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)
