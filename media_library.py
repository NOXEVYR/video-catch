"""Private saved-media catalogue for videos and PNG screenshots.

The catalogue never deletes media. Recycle intent is journaled before a Shell
operation so an index write failure cannot resurrect a playable-looking card.
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import time
import uuid

VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.mov', '.webm', '.avi', '.m4v', '.ts', '.flv'}
IMAGE_EXTENSIONS = {'.png'}
MEDIA_EXTENSIONS = VIDEO_EXTENSIONS | IMAGE_EXTENSIONS
SOURCES = {'download', 'clip', 'record', 'screenshot', 'import', 'scan', 'legacy'}
SCHEMA = 2


def _media_type(path):
    return 'image' if Path(path).suffix.lower() in IMAGE_EXTENSIONS else 'video'


def _atomic_json(path, data):
    temporary = path.with_name(path.name + '-' + uuid.uuid4().hex + '.tmp')
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            # Cleanup must not turn an already committed atomic write into a
            # reported failure, which would roll back only the in-memory state.
            pass


class Library:
    def __init__(self, directory, enabled=True):
        self.path = Path(directory) / 'library.json'
        self.journal = Path(directory) / 'library-recycle-journal.json'
        self.enabled = enabled
        self.entries = {}
        self.error = ''
        self.readonly = False
        self._saved = {}
        self._migration = False
        self.unconfirmed_paths = set()
        if enabled:
            try:
                if self.path.exists():
                    if self.path.stat().st_size > 10 * 1024 * 1024:
                        raise ValueError('catalogue too large')
                    data = json.loads(self.path.read_text(encoding='utf-8'))
                    if data.get('schema') not in (1, SCHEMA) or not isinstance(data.get('entries'), dict):
                        raise ValueError('invalid catalogue')
                    self._migration = data['schema'] == 1
                    for key, raw in data['entries'].items():
                        if (not isinstance(key, str) or not isinstance(raw, dict) or
                                not isinstance(raw.get('path'), str) or not Path(raw['path']).is_absolute() or
                                not isinstance(raw.get('name'), str) or type(raw.get('hidden')) is not bool or
                                type(raw.get('added')) not in (int, float)):
                            raise ValueError('invalid entry')
                        item = dict(raw)
                        item.setdefault('media_type', _media_type(item['path']))
                        item.setdefault('source', 'legacy')
                        item.setdefault('recycled', False)
                        if (item['media_type'] not in {'video', 'image'} or item['source'] not in SOURCES or
                                type(item['recycled']) is not bool):
                            raise ValueError('invalid entry details')
                        self.entries[key] = item
            except (OSError, ValueError, TypeError, AttributeError):
                self.error = '素材目录读取失败，原目录文件已保护。'
                self.readonly = True
        self._saved = deepcopy(self.entries)
        if enabled and not self.readonly:
            try:
                if self.journal.exists():
                    self.recover_recycle()
            except OSError:
                self.readonly = True
                self.error = '回收事务无法读取，素材索引暂以只读方式保护。'

    @staticmethod
    def key(path):
        return os.path.normcase(str(Path(path).resolve()))

    def add(self, path, name='', restore=False, source='import'):
        try:
            path = Path(path).resolve()
            if path.suffix.lower() not in MEDIA_EXTENSIONS or not path.is_file():
                return False
            key = self.key(path)
        except (OSError, RuntimeError):
            self.error = '部分素材暂时无法访问，请检查文件位置和权限。'
            return False
        existing = self.entries.get(key)
        if existing:
            if restore and existing['hidden']:
                existing['hidden'] = False
                existing['recycled'] = False
                existing['source'] = source if source in SOURCES else 'import'
                return True
            return False
        if len(self.entries) >= 5000:
            self.error = '素材目录已达 5000 条，请先整理目录。'
            return False
        self.entries[key] = {'path': str(path), 'name': name or path.name,
                             'hidden': False, 'recycled': False,
                             'media_type': _media_type(path),
                             'source': source if source in SOURCES else 'import',
                             'added': time.time()}
        return True

    def collect(self, items):
        changed = False
        for item in items:
            if isinstance(item, dict) and item.get('status') == '已保存' and item.get('path'):
                changed = self.add(item['path'], item.get('display_name', ''),
                                   source=item.get('operation', 'download')) or changed
        return self.save() if changed else False

    def _backup_schema1(self):
        if not self._migration or not self.enabled:
            return
        backup = self.path.with_name('library.schema1-backup-' + uuid.uuid4().hex + '.json')
        with self.path.open('rb') as source, backup.open('xb') as target:
            for block in iter(lambda: source.read(1024 * 1024), b''):
                target.write(block)
            target.flush()
            os.fsync(target.fileno())
        self._migration = False

    def save(self):
        if not self.enabled:
            self._saved = deepcopy(self.entries)
            return True
        if self.readonly:
            self.entries = deepcopy(self._saved)
            return False
        try:
            self._backup_schema1()
            _atomic_json(self.path, {'schema': SCHEMA, 'entries': self.entries})
            self._saved = deepcopy(self.entries)
            self.error = ''
            return True
        except OSError:
            self.entries = deepcopy(self._saved)
            self.error = '素材目录保存失败，本次修改可能无法在重启后保留。'
            return False

    def rows(self, query='', media_type='all', source='all', sort='recent'):
        query = query.casefold()
        rows = [(key, item) for key, item in self.entries.items()
                if not item['hidden'] and query in item['name'].casefold()
                and (media_type == 'all' or item['media_type'] == media_type)
                and (source == 'all' or item['source'] == source)]
        if sort == 'name':
            return sorted(rows, key=lambda row: (row[1]['name'].casefold(), row[0]))
        return sorted(rows, key=lambda row: (row[1]['added'], row[0]), reverse=True)

    def rename(self, key, name):
        name = name.strip()
        if not name or len(name) > 120 or any(c in name for c in '\r\n\0'):
            raise ValueError('名称须为 1～120 个字符，不能包含换行。')
        self.entries[key]['name'] = name
        return self.save()

    def remove(self, keys):
        for key in keys:
            if key in self.entries:
                self.entries[key]['hidden'] = True
        return self.save()

    def begin_recycle(self, keys):
        """Persist intent before Shell moves any original file."""
        if not self.enabled or self.readonly:
            self.error = '素材索引不可写，不能安全记录回收事务。'
            return False
        try:
            if self.journal.exists() or not keys:
                self.error = '存在待恢复的回收事务，或未选择素材。'
                return False
        except OSError:
            self.error = '回收事务无法访问，未移动任何文件。'
            return False
        operations = [{'key': key, 'path': self.entries[key]['path'], 'status': 'pending'}
                      for key in keys if key in self.entries and not self.entries[key]['hidden']]
        if len(operations) != len(set(keys)):
            self.error = '选择已变化，请重新选择素材。'
            return False
        try:
            _atomic_json(self.journal, {'schema': 2, 'operations': operations})
            return True
        except OSError:
            self.error = '回收事务无法保存，未移动任何文件。'
            return False

    def record_recycle_result(self, result):
        """Persist each Shell-confirmed result before the next file is moved.

        Called by the file worker; no entries or Tk objects are changed here.
        A journal write failure raises and stops further destructive operations.
        """
        data = json.loads(self.journal.read_text(encoding='utf-8'))
        if data.get('schema') != 2:
            raise ValueError('Unsupported recycle journal')
        for operation in data['operations']:
            if operation['path'] == result.path and operation['status'] == 'pending':
                operation['status'] = 'confirmed' if result.ok else 'failed'
                _atomic_json(self.journal, data)
                return
        raise ValueError('Recycle result does not match pending transaction')

    def recover_recycle(self):
        """Apply durable confirmed results; disappearance alone is never success."""
        try:
            if not self.journal.exists():
                return True
            if self.journal.stat().st_size > 2 * 1024 * 1024:
                raise ValueError('recycle journal too large')
            data = json.loads(self.journal.read_text(encoding='utf-8'))
            if (data.get('schema') not in (1, 2) or not isinstance(data.get('operations'), list) or
                    len(data['operations']) > 5000):
                raise ValueError('invalid recycle journal')
            confirmed, uncertain = [], []
            for operation in data['operations']:
                key, path = operation['key'], operation['path']
                if key not in self.entries or self.entries[key]['path'] != path:
                    raise ValueError('recycle journal mismatch')
                status = operation.get('status', 'pending')
                if status not in {'pending', 'confirmed', 'failed'}:
                    raise ValueError('invalid recycle result')
                if status == 'confirmed':
                    confirmed.append(key)
                elif status == 'pending' and not Path(path).is_file():
                    uncertain.append(key)
            for key in confirmed:
                self.entries[key]['hidden'] = True
                self.entries[key]['recycled'] = True
            if confirmed and not self.save():
                for key in confirmed:
                    self.entries[key]['hidden'] = True
                    self.entries[key]['recycled'] = True
                self.error = '回收站已确认，但索引保存失败；回收事务保留以供下次恢复。'
                return False
            if uncertain:
                self.unconfirmed_paths = set(uncertain)
                self.error = '部分回收结果未确认，请检查原位置和回收站；记录与事务已保留。'
                return False
            self.unconfirmed_paths.clear()
            self.journal.unlink()
            return True
        except (OSError, ValueError, TypeError, KeyError):
            self.error = '回收事务恢复失败，原记录和事务文件已保留。'
            return False
