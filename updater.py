"""Update check against this project's GitHub Releases.

Release builds look at the latest published GitHub release and compare its tag
(e.g. "v1.0.3") with version.__version__. This only *tells* the operator a newer
build exists and opens the download in their browser — it never downloads or
replaces the running app itself, and it never interrupts: the main window shows a
dismissible banner, and Settings → Help has a manual "Check for updates" button.

Uses QtNetwork (not urllib) so HTTPS goes through the OS certificate store on
Windows/macOS and the request runs asynchronously, without blocking the UI.
Running from source (version "dev") never checks.
"""
import json
import re
import sys
from dataclasses import asdict, dataclass

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

import version
from paths import app_data_dir

REPO = 'pongyu/scripture-cast'
API_URL = f'https://api.github.com/repos/{REPO}/releases/latest'
RELEASES_URL = f'https://github.com/{REPO}/releases/latest'
CONFIG_PATH = app_data_dir() / 'bible-presenter' / 'update_config.json'
REQUEST_TIMEOUT_MS = 10_000

# Check-state values.
IDLE = 'idle'
CHECKING = 'checking'
UP_TO_DATE = 'up_to_date'
AVAILABLE = 'available'
ERROR = 'error'
UNSUPPORTED = 'unsupported'  # running from source, so there's no release version to compare against


@dataclass
class UpdateConfig:
    check_on_startup: bool = True

    @classmethod
    def load(cls) -> 'UpdateConfig':
        try:
            data = json.loads(CONFIG_PATH.read_text())
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except (FileNotFoundError, json.JSONDecodeError, TypeError):
            return cls()

    def save(self):
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_PATH.write_text(json.dumps(asdict(self), indent=2))
        except OSError:
            pass  # a preference that fails to persist isn't worth interrupting the operator over


@dataclass
class ReleaseInfo:
    version: str       # "1.0.3" (tag without the leading "v")
    page_url: str      # the release's GitHub page ("what's new")
    download_url: str  # direct download for this OS if the release has one, else page_url


def parse_version(text: str) -> tuple[int, ...] | None:
    """"v1.0.3" / "1.0.3" -> (1, 0, 3). None if it doesn't look like a version at all."""
    match = re.search(r'(\d+)\.(\d+)(?:\.(\d+))?', text or '')
    if not match:
        return None
    return tuple(int(part or 0) for part in match.groups())


def is_newer(candidate: str, current: str) -> bool:
    candidate_parts, current_parts = parse_version(candidate), parse_version(current)
    return bool(candidate_parts and current_parts and candidate_parts > current_parts)


def _pick_download_url(assets: list[dict], fallback: str) -> str:
    """The release asset for the OS we're running on (the CI publishes a Windows .exe and a
    macOS .zip), so "Download" starts the right file in one click."""
    wanted_suffix = '.exe' if sys.platform == 'win32' else '.zip' if sys.platform == 'darwin' else None
    if wanted_suffix:
        for asset in assets:
            if str(asset.get('name', '')).lower().endswith(wanted_suffix) and asset.get('browser_download_url'):
                return asset['browser_download_url']
    return fallback


class UpdateChecker(QObject):
    state_changed = Signal()

    def __init__(self):
        super().__init__()
        self.config = UpdateConfig.load()
        self.current_version = version.__version__
        self.state = UNSUPPORTED if self.is_dev else IDLE
        self.latest: ReleaseInfo | None = None
        self.error = ''
        self.manual = False  # whether the in-flight/last check was user-initiated
        self._manager: QNetworkAccessManager | None = None

    @property
    def is_dev(self) -> bool:
        return self.current_version == 'dev'

    def set_check_on_startup(self, enabled: bool):
        self.config.check_on_startup = enabled
        self.config.save()

    def check(self, manual: bool = False):
        """Ask GitHub for the latest release. Results arrive via state_changed. Automatic
        (startup) checks stay silent on failure; manual ones report it."""
        if self.is_dev:
            if manual:
                self._set_state(UNSUPPORTED)
            return
        if self.state == CHECKING:
            self.manual = self.manual or manual
            return
        self.manual = manual
        self.error = ''
        self._set_state(CHECKING)

        if self._manager is None:
            self._manager = QNetworkAccessManager(self)
        request = QNetworkRequest(QUrl(API_URL))
        request.setRawHeader(b'Accept', b'application/vnd.github+json')
        request.setRawHeader(b'User-Agent', f'ScriptureCast/{self.current_version}'.encode())
        request.setTransferTimeout(REQUEST_TIMEOUT_MS)
        reply = self._manager.get(request)
        reply.finished.connect(lambda: self._on_reply(reply))

    def _on_reply(self, reply: QNetworkReply):
        try:
            status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            if reply.error() != QNetworkReply.NetworkError.NoError and status is None:
                self._fail("Couldn't reach GitHub. Check your internet connection and try again.")
                return
            if status == 404:
                # No published release yet — nothing newer than what's running.
                self._set_state(UP_TO_DATE)
                return
            if status != 200:
                self._fail("GitHub didn't respond as expected. Try again in a little while.")
                return
            try:
                data = json.loads(bytes(reply.readAll()).decode('utf-8'))
                tag = str(data['tag_name'])
            except (ValueError, KeyError, TypeError):
                self._fail("Couldn't read GitHub's response. Try again in a little while.")
                return

            if data.get('draft') or data.get('prerelease') or not is_newer(tag, self.current_version):
                self.latest = None
                self._set_state(UP_TO_DATE)
                return
            page_url = str(data.get('html_url') or RELEASES_URL)
            self.latest = ReleaseInfo(
                version=tag.lstrip('vV'),
                page_url=page_url,
                download_url=_pick_download_url(data.get('assets') or [], page_url),
            )
            self._set_state(AVAILABLE)
        finally:
            reply.deleteLater()

    def _fail(self, message: str):
        self.error = message
        self._set_state(ERROR)

    def _set_state(self, state: str):
        self.state = state
        self.state_changed.emit()

    def status_text(self) -> str:
        """One-line, plain-language status for the Settings → Help panel."""
        if self.state == CHECKING:
            return 'Checking for updates…'
        if self.state == UP_TO_DATE:
            return f"You're up to date (v{self.current_version})."
        if self.state == AVAILABLE and self.latest:
            return f'Version {self.latest.version} is available.'
        if self.state == ERROR:
            return self.error
        if self.state == UNSUPPORTED:
            return 'Running from source — update checks only apply to release builds.'
        return ''


instance = UpdateChecker()
