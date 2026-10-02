"""Stable errors shared by backup storage boundaries."""

class BackupStorageError(RuntimeError):
    """Raised when a backup operation fails, carrying a stable diagnostic code."""

    def __init__(self, code: str, message: str) -> None:
        """Store the diagnostic code with the human-readable message."""
        self.code = code
        super().__init__(message)

