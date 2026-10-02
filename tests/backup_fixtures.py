"""Realistic temporary profile and settings fixtures for backup and restore checks."""
from pathlib import Path
from types import SimpleNamespace
from dayz_serverman.domain.profiles import ProfileInput, ProfileRecord

class FakeProfiles:
    """Profile port stub serving one fixed record."""
    def __init__(self, record: ProfileRecord) -> None:
        """Store the record returned by every read."""
        self.record = record

    def read(self, _profile_id: object) -> ProfileRecord:
        """Return the stored record regardless of the requested id."""
        return self.record


class FakeSettings:
    """Settings stub exposing a fixed root and backup destination."""
    def __init__(self, dayz: Path, default: Path, custom: Path | None = None) -> None:
        """Build the settings value from the dayz root and destination paths."""
        self.value = SimpleNamespace(
            dayz_root=str(dayz), revision=4,
            custom_backup_root=str(custom) if custom else None,
        )
        self.default = default

    def load(self) -> object:
        """Return the fixed settings value."""
        return self.value

    def backup_root(self, settings: object | None = None) -> Path:
        """Return the custom backup root or the default destination."""
        current = settings or self.value
        return Path(current.custom_backup_root) if current.custom_backup_root else self.default


def record(mission: bool = True, runtime_profile: str | None = "profiles\\main") -> ProfileRecord:
    """Build the fixed profile record used by the backup tests."""
    return ProfileRecord(3, ProfileInput(
        "main", "Máin Profile", "DayZServer_x64.exe", "Config Files\\serverDZ.cfg",
        "mpmissions\\dayzOffline.chernarusplus" if mission else None,
        2302, (), (), runtime_profile,
    ))


def create_runtime_profile(dayz: Path, relative: str = "profiles\\main") -> Path:
    """Create the runtime profile tree with nested Unicode content."""
    runtime = dayz.joinpath(*relative.split("\\"))
    (runtime / "nested").mkdir(parents=True, exist_ok=True)
    (runtime / "settings.json").write_text('{"fixture":true}\n', encoding="utf-8")
    (runtime / "nested" / "Állapot.txt").write_text("runtime\n", encoding="utf-8")
    return runtime

