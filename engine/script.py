"""剧本解析与加载期校验。

剧本为单文件 Markdown + YAML frontmatter（见交接文档 §4）。本模块负责：
- 解析 frontmatter 六字段与正文的「## 章节: <id>」小节；
- 加载期校验（启动即报错，绝不带病进入游戏）。
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import yaml

from . import condition
from .state import DEFAULT_BY_TYPE, type_ok


class ScriptError(ValueError):
    """剧本加载期错误。"""


@dataclass
class Chapter:
    id: str
    title: str
    summary: str
    when_expr: Optional[str] = None
    when: Optional[List[Any]] = None   # 已解析校验的衔接条件子句；None 表示无条件
    body: str = ""


@dataclass
class Variable:
    name: str
    type: str
    default: Any


@dataclass
class Ending:
    id: str
    name: str
    when_expr: str
    when: List[Any]  # 已解析校验的条件子句
    priority: int = 0  # 自动结局的结算优先级（越大越优先；同值时按 frontmatter 顺序）
    auto: bool = False  # True：条件满足即由引擎自动结算，无需模型声明


class Script:
    def __init__(self, title, intro, constant_world, constant_characters,
                 chapters, variables, endings, bodies, source_path, warnings,
                 max_turns=None, confirm_ending=False):
        self.title = title
        self.intro = intro
        self.constant_world = constant_world
        self.constant_characters = constant_characters
        self.chapters = chapters
        self.variables = variables
        self.endings = endings
        self.source_path = source_path
        self.warnings = warnings
        self.max_turns = max_turns  # 可选回合上限；None 表示不限
        self.confirm_ending = confirm_ending  # True 时命中结局先挂起等待玩家确认
        self.var_types = {v.name: v.type for v in variables}
        self.chapter_by_id = {c.id: c for c in chapters}
        self.ending_by_id = {e.id: e for e in endings}
        # 自动结局按优先级降序（稳定排序，同优先级保持 frontmatter 顺序）
        self.auto_endings = sorted(
            (e for e in endings if e.auto), key=lambda e: -e.priority
        )
        for c in chapters:
            c.body = bodies.get(c.id, "")


_HEADER_RE = re.compile(r'^## *章节 *[:：] *(.+?) *$', re.MULTILINE)


def _split_frontmatter(text):
    lines = text.split('\n')
    if not lines or lines[0].strip() != '---':
        raise ScriptError('缺少 YAML frontmatter：文件应以 --- 开头')
    end = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == '---':
            end = idx
            break
    if end is None:
        raise ScriptError('frontmatter 缺少结束标记 ---')
    fm = '\n'.join(lines[1:end])
    body = '\n'.join(lines[end + 1:])
    return fm, body


def _parse_bodies(body):
    matches = list(_HEADER_RE.finditer(body))
    if not matches:
        raise ScriptError('正文缺少任何「## 章节: <id>」小节')
    bodies = {}
    for idx, m in enumerate(matches):
        cid = m.group(1).strip()
        if cid in bodies:
            raise ScriptError('正文出现重复的章节小节：' + cid)
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(body)
        bodies[cid] = body[start:end].strip()
    return bodies


def _parse_condition(expr, var_types, ctx):
    try:
        clauses = condition.parse(expr)
        condition.validate(clauses, var_types)
        return clauses
    except condition.ConditionError as e:
        raise ScriptError(ctx + ' 错误：' + str(e))


def _parse_variables(raw):
    if not isinstance(raw, list):
        raise ScriptError('variables 必须为列表')
    variables = []
    seen = set()
    for idx, v in enumerate(raw):
        if not isinstance(v, dict):
            raise ScriptError('variables[' + str(idx) + '] 必须为映射')
        name = v.get('name')
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
            raise ScriptError('variables[' + str(idx) + '] 的 name 非法（需为合法标识符）：' + repr(name))
        if name in seen:
            raise ScriptError('variables 中存在重复 name：' + name)
        seen.add(name)
        vtype = v.get('type')
        if vtype not in DEFAULT_BY_TYPE:
            raise ScriptError('变量 ' + repr(name) + ' 的 type 非法：' + repr(vtype) + '（应为 bool/int/str/str[]）')
        default = v.get('default', copy.deepcopy(DEFAULT_BY_TYPE[vtype]))
        if not type_ok(vtype, default):
            raise ScriptError('变量 ' + repr(name) + ' 的 default 与 type ' + vtype + ' 不符')
        variables.append(Variable(name, vtype, copy.deepcopy(default)))
    return variables


def _parse_chapters(raw, var_types):
    if not isinstance(raw, list) or not raw:
        raise ScriptError('chapters 必须为非空列表')
    chapters = []
    seen = set()
    for idx, ch in enumerate(raw):
        if not isinstance(ch, dict):
            raise ScriptError('chapters[' + str(idx) + '] 必须为映射')
        cid = ch.get('id')
        if not isinstance(cid, str) or not cid.strip():
            raise ScriptError('chapters[' + str(idx) + '] 缺少 id')
        cid = cid.strip()
        if cid in seen:
            raise ScriptError('chapters 中存在重复 id：' + cid)
        seen.add(cid)
        title = (ch.get('title') or cid)
        summary = (ch.get('summary') or '')
        when_expr = ch.get('when')
        when = None
        if when_expr is not None:
            when = _parse_condition(when_expr, var_types, '章节 ' + repr(cid) + ' 的衔接条件')
        chapters.append(Chapter(cid, title, summary, when_expr, when))
    return chapters


def _parse_endings(raw, var_types):
    if not isinstance(raw, list) or not raw:
        raise ScriptError('endings 必须为非空列表')
    endings = []
    seen = set()
    for idx, e in enumerate(raw):
        if not isinstance(e, dict):
            raise ScriptError('endings[' + str(idx) + '] 必须为映射')
        eid = e.get('id')
        if not isinstance(eid, str) or not eid.strip():
            raise ScriptError('endings[' + str(idx) + '] 缺少 id')
        eid = eid.strip()
        if eid in seen:
            raise ScriptError('endings 中存在重复 id：' + eid)
        seen.add(eid)
        name = e.get('name') or eid
        when_expr = e.get('when')
        if not isinstance(when_expr, str) or not when_expr.strip():
            raise ScriptError('结局 ' + repr(eid) + ' 缺少 when 条件')
        when = _parse_condition(when_expr, var_types, '结局 ' + repr(eid) + ' 的条件')
        auto = e.get('auto', False)
        if not isinstance(auto, bool):
            raise ScriptError('结局 ' + repr(eid) + ' 的 auto 必须为 true/false')
        priority = e.get('priority', 0)
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise ScriptError('结局 ' + repr(eid) + ' 的 priority 必须为整数')
        endings.append(Ending(eid, name, when_expr, when, priority=priority, auto=auto))
    return endings


def parse_script(text, source_path='<string>'):
    warnings = []
    fm_text, body = _split_frontmatter(text)
    try:
        data = yaml.safe_load(fm_text)
    except yaml.YAMLError as e:
        raise ScriptError('frontmatter YAML 解析失败：' + str(e))
    if not isinstance(data, dict):
        raise ScriptError('frontmatter 内容必须为 YAML 映射')

    for field in ('title', 'chapters', 'variables', 'endings'):
        if field not in data or data[field] is None:
            raise ScriptError('frontmatter 缺少必需字段：' + field)

    title = data['title']
    if not isinstance(title, str) or not title.strip():
        raise ScriptError('title 必须为非空字符串')
    title = title.strip()

    intro = data.get('intro') or ''
    if not isinstance(intro, str):
        intro = str(intro)

    const = data.get('constant') or {}
    if not isinstance(const, dict):
        raise ScriptError('constant 必须为映射（含 world / characters）')
    world = const.get('world') or ''
    characters = const.get('characters') or ''
    if not isinstance(world, str):
        world = str(world)
    if not isinstance(characters, str):
        characters = str(characters)

    variables = _parse_variables(data['variables'])
    var_types = {v.name: v.type for v in variables}
    chapters = _parse_chapters(data['chapters'], var_types)
    endings = _parse_endings(data['endings'], var_types)

    max_turns = data.get('max_turns')
    if max_turns is not None:
        if isinstance(max_turns, bool) or not isinstance(max_turns, int) or max_turns < 1:
            raise ScriptError('max_turns 必须为正整数（或省略表示不限）')
    confirm_ending = data.get('confirm_ending', False)
    if not isinstance(confirm_ending, bool):
        raise ScriptError('confirm_ending 必须为 true/false')

    bodies = _parse_bodies(body)

    for ch in chapters:
        if ch.id not in bodies:
            raise ScriptError('正文缺少章节 ' + repr(ch.id) + ' 对应的「## 章节:」小节')
    for cid in bodies:
        if cid not in {c.id for c in chapters}:
            warnings.append('正文存在 frontmatter 未声明的章节小节：' + cid)

    return Script(title, intro, world, characters, chapters, variables,
                  endings, bodies, source_path, warnings,
                  max_turns=max_turns, confirm_ending=confirm_ending)


def load_script(path):
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()
    return parse_script(text, path)
