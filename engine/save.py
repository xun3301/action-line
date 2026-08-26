"""存档/读档：多槽位 + .bak + 滚动上限。"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 1


class SaveError(Exception):
    """存档读取失败。"""


def _sanitize(name: str) -> str:
    """把名字净化为安全的文件名片段（去掉路径分隔与非法字符）。"""
    out = []
    for ch in name:
        if ch.isalnum() or ch in ' _-·':
            out.append(ch)
        else:
            out.append('_')
    return ''.join(out).strip()


def _safe_dirname(title: str) -> str:
    """把剧本名转成安全的目录名（去掉 Windows 非法字符）。"""
    return _sanitize(title) or 'script'


def script_dir(saves_root: str, script_title: str) -> str:
    return os.path.join(saves_root, _safe_dirname(script_title))


def _unique_name(d: str, base: str) -> str:
    name = base
    i = 1
    while os.path.exists(os.path.join(d, name + '.json')):
        name = base + '_' + str(i)
        i += 1
    return name


def write_save(saves_root, script_title, data, max_saves, name=None) -> str:
    d = script_dir(saves_root, script_title)
    os.makedirs(d, exist_ok=True)

    safe = _sanitize(name) if name else ''
    if safe:
        # 具名存档：名字写入文件名，同名覆盖（可复用槽位）
        fname = safe + '.json'
    else:
        base = datetime.now().strftime('%Y%m%d_%H%M%S')
        fname = _unique_name(d, base) + '.json'

    payload = dict(data)
    payload['schema_version'] = SCHEMA_VERSION
    payload['saved_at'] = datetime.now().isoformat(timespec='seconds')

    text = json.dumps(payload, ensure_ascii=False, indent=2)
    path = os.path.join(d, fname)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    with open(path + '.bak', 'w', encoding='utf-8') as f:
        f.write(text)

    _roll(d, max_saves)
    return path


def _roll(d: str, max_saves: int) -> None:
    files = sorted(
        [f for f in os.listdir(d) if f.endswith('.json') and not f.endswith('.bak')],
        key=lambda f: os.path.getmtime(os.path.join(d, f)),
    )
    while len(files) > max_saves:
        oldest = files.pop(0)
        p = os.path.join(d, oldest)
        for target in (p, p + '.bak'):
            try:
                os.remove(target)
            except OSError:
                pass


def list_saves(saves_root, script_title) -> List[str]:
    d = script_dir(saves_root, script_title)
    if not os.path.isdir(d):
        return []
    out = [os.path.join(d, f) for f in os.listdir(d)
           if f.endswith('.json') and not f.endswith('.bak')]
    out.sort(reverse=True)
    return out


def list_backups(saves_root, script_title) -> List[str]:
    d = script_dir(saves_root, script_title)
    if not os.path.isdir(d):
        return []
    return sorted(f for f in os.listdir(d) if f.endswith('.bak'))


def read_save(path) -> Dict[str, Any]:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        raise SaveError('存档读取失败：' + str(e))
    if not isinstance(data, dict):
        raise SaveError('存档内容不是有效的对象')
    ver = data.get('schema_version')
    if isinstance(ver, int) and ver > SCHEMA_VERSION:
        raise SaveError(
            '存档 schema 版本 ' + str(ver) + ' 高于当前引擎版本 '
            + str(SCHEMA_VERSION) + '，无法读取'
        )
    return data
