"""Bounded private trim selections. Source paths are hashed; no media is copied."""
import hashlib
import json
import os
from pathlib import Path
import tempfile


class ClipDrafts:
    def __init__(self, directory, enabled=True):
        self.path = Path(directory) / 'clip-drafts.json'
        self.enabled, self.blocked = enabled, False
        self.entries = {}
        if enabled:
            try:
                with self.path.open('rb') as stream:
                    raw = stream.read(65537)
                if len(raw) > 65536:
                    raise ValueError('too large')
                value = json.loads(raw)
                if set(value) != {'schema', 'entries'} or value['schema'] != 1:
                    raise ValueError('schema')
                entries = value['entries']
                if not isinstance(entries, dict) or len(entries) > 32:
                    raise ValueError('entries')
                for key, row in entries.items():
                    if len(key) != 64 or any(c not in '0123456789abcdef' for c in key):
                        raise ValueError('key')
                    if (not isinstance(row, list) or len(row) != 6
                            or any(type(n) is not int or n < 0 for n in row) or row[3] > row[4]):
                        raise ValueError('selection')
                self.entries = entries
            except FileNotFoundError:
                pass
            except (OSError, ValueError, TypeError, KeyError):
                self.blocked = True  # Preserve unreadable/future files without overwriting.

    @staticmethod
    def key(source):
        return hashlib.sha256(os.path.normcase(str(Path(source).resolve())).encode('utf-8')).hexdigest()

    def get(self, source):
        row = self.entries.get(self.key(source))
        return (tuple(row[:3]), *row[3:]) if row else None

    def save(self, source, value=None):
        return self.save_many([(source, value)])

    def save_many(self, changes):
        if not changes:
            return True
        if self.blocked:
            return False
        updated = dict(self.entries)
        for source, value in changes:
            key = self.key(source)
            updated.pop(key, None)
            if value is not None:
                updated[key] = [*value[0], *value[1:]]
        while len(updated) > 32:
            updated.pop(next(iter(updated)))
        temporary = None
        try:
            if self.enabled:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                fd, temporary = tempfile.mkstemp(prefix='.clip-draft-', dir=self.path.parent)
                with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                    json.dump({'schema': 1, 'entries': updated}, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            self.entries = updated
            return True
        except OSError:
            return False
        finally:
            if temporary:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    pass
