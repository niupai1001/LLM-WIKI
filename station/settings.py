"""Local topic/subscription configuration, with optimistic concurrency and locked writes."""
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import threading
import uuid
from contextlib import closing, contextmanager
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .common import DB, META, PENDING, RAW, canonical

from .workspaces import ContextPath
CONFIG_DIR = ContextPath('config', Path(__file__).resolve().parent.parent / 'config')
TOPICS_FILE = CONFIG_DIR / 'categories.json'
FEEDS_FILE = CONFIG_DIR / 'feeds.json'
LOCK_FILE = CONFIG_DIR / '.settings.lock'
_ID = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?')
_THREAD_LOCK = threading.RLock()
KINDS = {'paper', 'article', 'news', 'note', 'document'}


class SettingsConflict(ValueError):
    """The browser edited an older configuration version."""


@contextmanager
def _locked():
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _THREAD_LOCK, LOCK_FILE.open('a+b') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _text(value, label, limit, required=False):
    if not isinstance(value, str):
        raise ValueError(f'{label}必须是文字')
    result = value.strip()
    if (required and not result) or len(result) > limit:
        raise ValueError(f'{label}不能为空且最多 {limit} 字' if required else f'{label}最多 {limit} 字')
    if any(ord(c) < 32 and c not in '\n\t' for c in result):
        raise ValueError(f'{label}含有无效字符')
    return result


def _enabled(value):
    if not isinstance(value, bool):
        raise ValueError('启用状态必须是布尔值')
    return value


def _key(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError('配置标识无效，请刷新页面后重试')
    return value


def _keywords(value):
    if not isinstance(value, list) or len(value) > 50:
        raise ValueError('每组关键词必须是列表，最多 50 条')
    result, seen = [], set()
    for item in value:
        word = _text(item, '关键词', 120, required=True)
        if word.casefold() not in seen:
            result.append(word)
            seen.add(word.casefold())
    return result


def _age(value):
    if value is None or value == '':
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 3650:
        raise ValueError('文章时限须为 1–3650 天的整数，留空表示不限')
    return value


def _url(value):
    value = _text(value, '订阅地址', 2048, required=True)
    if any(c.isspace() or ord(c) < 32 for c in value) or '\\' in value:
        raise ValueError('订阅地址格式无效')
    # Import only here: collector consumes the settings in the main application.
    from .collector import public_http
    if not public_http(value):
        raise ValueError('订阅地址须为无账号密码的公开 HTTP(S) 地址，端口须为 80 或 443')
    parsed = urlsplit(value)
    host = parsed.hostname
    if ':' not in host:
        try:
            ascii_host = host.encode('idna').decode('ascii')
        except UnicodeError:
            raise ValueError('订阅地址的域名无效') from None
        if (len(ascii_host) > 253 or '.' not in ascii_host or not all(
                re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?', label)
                for label in ascii_host.rstrip('.').split('.'))):
            raise ValueError('订阅地址的域名无效')
        # Numeric forms such as 127.1 must not bypass literal IP validation.
        if re.fullmatch(r'[0-9.]+', ascii_host):
            from ipaddress import ip_address
            try:
                if not ip_address(ascii_host).is_global:
                    raise ValueError('订阅地址必须指向公开网络')
            except ValueError:
                raise ValueError('订阅地址必须指向公开网络') from None
    # Hash/anchor does not affect a feed fetch and is removed from the stored URL.
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path, parsed.query, ''))


def _read():
    try:
        topic_bytes, feed_bytes = TOPICS_FILE.read_bytes(), FEEDS_FILE.read_bytes()
        topics, feeds = json.loads(topic_bytes), json.loads(feed_bytes)
    except (OSError, ValueError):
        raise ValueError('主题或订阅配置无法读取，请检查配置文件') from None
    if not isinstance(topics, list) or not isinstance(feeds, list):
        raise ValueError('主题和订阅配置必须是列表')
    seen = set()
    normalized_topics = []
    for item in topics:
        if not isinstance(item, dict):
            raise ValueError('主题配置格式无效')
        entry = dict(item)
        key = _key(item.get('id'))
        if key in seen:
            raise ValueError('主题配置含重复标识')
        seen.add(key)
        entry.update(name=_text(item.get('name'), '主题名称', 80, True),
                     description=_text(item.get('description', ''), '主题描述', 2000),
                     enabled=_enabled(item.get('enabled', True)))
        normalized_topics.append(entry)
    normalized_feeds, seen = [], set()
    for item in feeds:
        if not isinstance(item, dict):
            raise ValueError('订阅配置格式无效')
        entry = dict(item)
        key = item.get('id')
        if key is None:
            identity = json.dumps([canonical(str(item.get('url', ''))), item.get('name'), item.get('kind', 'article'), item.get('category', 'other')], ensure_ascii=False)
            key = 'feed-' + hashlib.sha256(identity.encode()).hexdigest()[:16]
        _key(key)
        if key in seen:
            raise ValueError('订阅配置含重复标识')
        seen.add(key)
        entry.update(id=key, enabled=_enabled(item.get('enabled', True)))
        entry.setdefault('kind', 'article')
        entry.setdefault('category', 'other')
        for field in ('include_keywords', 'require_keywords', 'exclude_keywords'):
            entry.setdefault(field, [])
        entry.setdefault('max_age_days', None)
        normalized_feeds.append(entry)
    version = hashlib.sha256(topic_bytes + b'\0' + feed_bytes).hexdigest()
    return {'version': version, 'topics': normalized_topics, 'feeds': normalized_feeds}


def snapshot():
    """Read all topics, including disabled ones; never rewrite configuration on GET."""
    with _locked():
        return _read()


def _check_payload(payload, current):
    if not isinstance(payload, dict):
        raise ValueError('请提交有效的配置内容')
    if payload.get('version') != current['version']:
        raise SettingsConflict('配置已被其他页面修改，请刷新后再保存')


def _atomic_bytes(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _persist(current):
    old = {path: path.read_bytes() for path in (TOPICS_FILE, FEEDS_FILE)}
    changed = []
    try:
        for path, key in ((TOPICS_FILE, 'topics'), (FEEDS_FILE, 'feeds')):
            content = (json.dumps(current[key], ensure_ascii=False, indent=2) + '\n').encode('utf-8')
            if content != old[path]:
                _atomic_bytes(path, content)
                changed.append(path)
    except OSError:
        try:
            for path in reversed(changed):
                _atomic_bytes(path, old[path])
        except OSError:
            raise ValueError('配置保存及恢复失败，请检查配置文件写入权限和磁盘空间') from None
        raise ValueError('配置保存失败，已恢复原配置，请检查文件写入权限') from None


def _result(message):
    return dict(_read(), message=message)


def save_topic(payload):
    with _locked():
        current = _read()
        _check_payload(payload, current)
        key = payload.get('id')
        existing = None
        if key is not None:
            _key(key)
            existing = next((t for t in current['topics'] if t['id'] == key), None)
            if existing is None:
                raise ValueError('主题已不存在，请刷新页面后重试')
        else:
            key = 'topic-' + uuid.uuid4().hex[:16]
        value = dict(existing or {})
        for field, default in (('name', ''), ('description', ''), ('enabled', True)):
            value[field] = payload.get(field, value.get(field, default))
        value.update(id=key, name=_text(value['name'], '主题名称', 80, True),
                     description=_text(value['description'], '主题描述', 2000), enabled=_enabled(value['enabled']))
        if any(t['id'] != key and t['name'].casefold() == value['name'].casefold() for t in current['topics']):
            raise ValueError('已存在同名主题，请使用其他名称')
        if existing:
            current['topics'][current['topics'].index(existing)] = value
        else:
            current['topics'].append(value)
        paused = 0
        if not value['enabled']:
            for feed in current['feeds']:
                if feed['category'] == key and feed['enabled']:
                    feed['enabled'] = False
                    paused += 1
        _persist(current)
        suffix = f'，同时停用了 {paused} 个关联订阅' if paused else ''
        return _result(('主题已保存' if existing else '主题已添加') + suffix)


def _topic_references(key, current):
    found = []
    count = sum(feed['category'] == key for feed in current['feeds'])
    if count:
        found.append(f'{count} 个订阅节点（含停用节点）')
    if DB.is_file():
        try:
            with closing(sqlite3.connect(DB.resolve().as_uri() + '?mode=ro', uri=True)) as db:
                table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sources'").fetchone()
                if table:
                    columns = {r[1] for r in db.execute('PRAGMA table_info(sources)')}
                    if 'category' in columns:
                        count = db.execute('SELECT count(*) FROM sources WHERE category=?', (key,)).fetchone()[0]
                        if count:
                            found.append(f'{count} 份原始资料')
        except sqlite3.Error:
            raise ValueError('无法检查资料引用，未删除主题；请检查资料数据库') from None
    for directory, label in ((RAW, '原始资料记录'), (META, 'Wiki 页面'), (PENDING, '待审核草稿')):
        count = 0
        for path in directory.glob('*.json'):
            try:
                record = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                raise ValueError(f'无法检查{label}引用，未删除主题；请检查资料文件') from None
            if isinstance(record, dict) and record.get('category') == key:
                count += 1
        if count:
            found.append(f'{count} 份{label}')
    return found


def delete_topic(key, payload):
    with _locked():
        current = _read()
        _check_payload(payload, current)
        _key(key)
        existing = next((t for t in current['topics'] if t['id'] == key), None)
        if existing is None:
            raise ValueError('主题已不存在，请刷新页面后重试')
        references = _topic_references(key, current)
        if references:
            raise ValueError('该主题仍被' + '、'.join(references) + '使用；可停用主题，删除前请处理这些引用')
        current['topics'].remove(existing)
        _persist(current)
        return _result('主题已删除')


def save_feed(payload):
    with _locked():
        current = _read()
        _check_payload(payload, current)
        key = payload.get('id')
        existing = None
        if key is not None:
            _key(key)
            existing = next((f for f in current['feeds'] if f['id'] == key), None)
            if existing is None:
                raise ValueError('订阅已不存在，请刷新页面后重试')
        else:
            key = 'feed-' + uuid.uuid4().hex[:16]
        value = dict(existing or {})
        for field, default in (('name', ''), ('url', ''), ('kind', 'article'), ('category', 'other'), ('enabled', True), ('include_keywords', []), ('require_keywords', []), ('exclude_keywords', []), ('max_age_days', None)):
            value[field] = payload.get(field, value.get(field, default))
        value.update(id=key, name=_text(value['name'], '订阅名称', 120, True), url=_url(value['url']), enabled=_enabled(value['enabled']))
        if not isinstance(value['kind'], str) or value['kind'] not in KINDS:
            raise ValueError('资料类型须为 paper、article、news、note 或 document')
        topic = next((t for t in current['topics'] if t['id'] == value['category']), None)
        if topic is None:
            raise ValueError('订阅所属主题不存在，请选择现有主题')
        if value['enabled'] and not topic['enabled']:
            raise ValueError('该主题已停用，请先启用主题，或停用此订阅')
        for field in ('include_keywords', 'require_keywords', 'exclude_keywords'):
            value[field] = _keywords(value[field])
        value['max_age_days'] = _age(value['max_age_days'])
        if (existing is None or canonical(existing['url']) != canonical(value['url'])) and any(f['id'] != key and canonical(f['url']) == canonical(value['url']) for f in current['feeds']):
            raise ValueError('已存在相同地址的订阅节点（含停用节点），请编辑或删除原节点')
        if existing:
            current['feeds'][current['feeds'].index(existing)] = value
        else:
            current['feeds'].append(value)
        _persist(current)
        return _result('订阅已保存' if existing else '订阅已添加')


def delete_feed(key, payload):
    with _locked():
        current = _read()
        _check_payload(payload, current)
        _key(key)
        existing = next((f for f in current['feeds'] if f['id'] == key), None)
        if existing is None:
            raise ValueError('订阅已不存在，请刷新页面后重试')
        current['feeds'].remove(existing)
        _persist(current)
        return _result('订阅已删除，已收录资料会继续保留')
