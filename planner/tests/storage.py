from io import BytesIO

from django.core.exceptions import SuspiciousFileOperation
from django.core.files import File
from django.core.files.storage import Storage


class MemoryStorage(Storage):
    """In-memory storage for the authenticated import/export workflow test."""
    files = {}

    def _open(self, name, mode="rb"):
        if name not in self.files:
            raise FileNotFoundError(name)
        return File(BytesIO(self.files[name]), name=name)

    def _save(self, name, content):
        self.files[name] = b"".join(content.chunks())
        return name

    def exists(self, name):
        return name in self.files

    def delete(self, name):
        self.files.pop(name, None)

    def url(self, name):
        if name not in self.files:
            raise SuspiciousFileOperation(name)
        return f"/test-media/{name}"
