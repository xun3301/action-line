"""条件表达式解析与求值。

语法（见交接文档 §4）：

    比较:   <var> == | != | > | >= | < | <= <字面量>
    列表:   <var> contains <字符串>
    连接:   &&  （不支持 || 和括号）

字面量：整数（含负数）、带引号字符串、未加引号的裸字符串（用于中文值）、true/false。

职责：
- 解析期：parse() 生成子句列表；validate() 依据变量表做存在性/类型校验（加载期报错用）。
- 运行期：evaluate() 依据当前状态值求值。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple


class ConditionError(ValueError):
    """条件表达式解析或校验错误。"""


@dataclass(frozen=True)
class Clause:
    var: str
    op: str       # '==' | '!=' | '>' | '>=' | '<' | '<=' | 'contains'
    literal: Any  # int | str | bool


_OP2 = ('==', '!=', '>=', '<=')
_LITERAL_KINDS = ('NUMBER', 'STRING', 'BOOL', 'IDENT', 'BARE')
_IDENT_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
_NUMBER_RE = re.compile(r'-?\d+')
_OPERATOR_CHARS = frozenset('&<>=!') | {'"', "'"}


def _unescape(s: str) -> str:
    """去掉字符串字面量中的转义引号/反斜杠。"""
    out: List[str] = []
    j = 0
    while j < len(s):
        if s[j] == '\\' and j + 1 < len(s) and s[j + 1] in ('"', "'", '\\'):
            out.append(s[j + 1])
            j += 2
            continue
        out.append(s[j])
        j += 1
    return ''.join(out)


def _tokenize(expr: str) -> List[Tuple[str, Any]]:
    """把条件串切成 token。token 形如 (kind, value)。

    kind：IDENT（变量名）/ NUMBER / STRING / BOOL / BARE（裸字符串字面量）
          / CONTAINS / OP / AND。
    """
    tokens: List[Tuple[str, Any]] = []
    i = 0
    n = len(expr)
    while i < n:
        ch = expr[i]
        if ch.isspace():
            i += 1
            continue

        two = expr[i:i + 2]
        if two in _OP2:
            tokens.append(('OP', two))
            i += 2
            continue
        if two == '&&':
            tokens.append(('AND', '&&'))
            i += 2
            continue
        if ch in '><':
            tokens.append(('OP', ch))
            i += 1
            continue

        if ch in ('"', "'"):
            quote = ch
            j = i + 1
            while j < n:
                if expr[j] == '\\' and j + 1 < n:
                    j += 2  # 跳过被转义的字符
                    continue
                if expr[j] == quote:
                    break
                j += 1
            if j >= n:
                raise ConditionError('字符串字面量未闭合：…' + expr[i:i + 16] + '…')
            tokens.append(('STRING', _unescape(expr[i + 1:j])))
            i = j + 1
            continue

        if ch in '&=!':
            raise ConditionError('条件中含无法识别的字符 ' + repr(ch) + '（位置 ' + str(i) + '）')

        # 其余：读一个“词”，直到空白或运算符/引号字符
        j = i
        while j < n and not expr[j].isspace() and expr[j] not in _OPERATOR_CHARS:
            j += 1
        word = expr[i:j]
        if not word:
            raise ConditionError('无法识别的字符 ' + repr(ch) + '（位置 ' + str(i) + '）')
        if _NUMBER_RE.fullmatch(word):
            tokens.append(('NUMBER', int(word)))
        elif _IDENT_RE.fullmatch(word):
            if word == 'contains':
                tokens.append(('CONTAINS', 'contains'))
            elif word == 'true':
                tokens.append(('BOOL', True))
            elif word == 'false':
                tokens.append(('BOOL', False))
            else:
                tokens.append(('IDENT', word))
        else:
            tokens.append(('BARE', word))
        i = j

    return tokens


def _literal_value(kind: str, value: Any) -> Any:
    # IDENT / BARE 出现在操作数位置时按裸字符串处理
    return value


def parse(expr: Optional[str]) -> List[Clause]:
    """把条件表达式解析为 AND 相连的子句列表。语法错误抛 ConditionError。"""
    if expr is None:
        raise ConditionError('条件为空')
    if not isinstance(expr, str):
        raise ConditionError('条件必须是字符串，收到 ' + type(expr).__name__)
    tokens = _tokenize(expr)
    if not tokens:
        raise ConditionError('条件为空')

    clauses: List[Clause] = []
    i = 0
    while i < len(tokens):
        kind, value = tokens[i]
        if kind != 'IDENT':
            raise ConditionError('条件子句应以变量名开头，却遇到 ' + repr(value))
        var = value
        i += 1
        if i >= len(tokens):
            raise ConditionError('变量 ' + repr(var) + ' 之后缺少运算符')

        kind, value = tokens[i]
        if kind == 'CONTAINS':
            i += 1
            if i >= len(tokens) or tokens[i][0] not in _LITERAL_KINDS:
                raise ConditionError('contains 之后应为字符串（变量 ' + repr(var) + '）')
            clauses.append(Clause(var, 'contains', _literal_value(*tokens[i])))
            i += 1
        elif kind == 'OP':
            op = value
            i += 1
            if i >= len(tokens) or tokens[i][0] not in _LITERAL_KINDS:
                raise ConditionError('运算符 ' + op + ' 之后应为字面量（变量 ' + repr(var) + '）')
            clauses.append(Clause(var, op, _literal_value(*tokens[i])))
            i += 1
        else:
            raise ConditionError('变量 ' + repr(var) + ' 之后应为运算符或 contains，却遇到 ' + repr(value))

        if i < len(tokens):
            if tokens[i][0] != 'AND':
                raise ConditionError('多个条件之间必须用 && 连接（不支持 || 与括号）')
            i += 1
            if i >= len(tokens):
                raise ConditionError('&& 之后缺少条件')

    return clauses


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def validate(clauses: Sequence[Clause], var_types: Dict[str, str]) -> None:
    """依据变量表校验子句的变量存在性与类型匹配。失败抛 ConditionError。"""
    for c in clauses:
        if c.var not in var_types:
            raise ConditionError('条件引用了未声明变量 ' + repr(c.var))
        vt = var_types[c.var]

        if c.op == 'contains':
            if vt != 'str[]':
                raise ConditionError(
                    'contains 仅可用于 str[] 变量，' + repr(c.var) + ' 的类型为 ' + vt
                )
            if not isinstance(c.literal, str):
                raise ConditionError('contains 之后应为字符串（变量 ' + repr(c.var) + '）')
        elif c.op in ('>', '>=', '<', '<='):
            if vt != 'int':
                raise ConditionError(
                    '运算符 ' + c.op + ' 仅可用于 int 变量，' + repr(c.var) + ' 的类型为 ' + vt
                )
            if not _is_int(c.literal):
                raise ConditionError('运算符 ' + c.op + ' 之后应为整数（变量 ' + repr(c.var) + '）')
        elif c.op in ('==', '!='):
            if vt == 'bool' and not isinstance(c.literal, bool):
                raise ConditionError('变量 ' + repr(c.var) + ' 为 bool，比较值应为 true/false')
            elif vt == 'int' and not _is_int(c.literal):
                raise ConditionError('变量 ' + repr(c.var) + ' 为 int，比较值应为整数')
            elif vt == 'str' and not isinstance(c.literal, str):
                raise ConditionError('变量 ' + repr(c.var) + ' 为 str，比较值应为字符串')
            elif vt == 'str[]':
                raise ConditionError('str[] 变量 ' + repr(c.var) + ' 请使用 contains，而非 ' + c.op)
        else:
            raise ConditionError('未知运算符 ' + repr(c.op))


def evaluate(clauses: Sequence[Clause], values: Dict[str, Any]) -> bool:
    """依据当前状态值求值（全部子句成立才为真）。"""
    ok, _unmet = evaluate_detail(clauses, values)
    return ok


def _clause_ok(c: Clause, values: Dict[str, Any]) -> bool:
    if c.var not in values:
        return False
    val = values[c.var]
    if c.op == 'contains':
        if not isinstance(val, list) or c.literal not in val:
            return False
    elif c.op == '==':
        if val != c.literal:
            return False
    elif c.op == '!=':
        if val == c.literal:
            return False
    elif c.op == '>':
        if not _is_int(val) or not (val > c.literal):
            return False
    elif c.op == '>=':
        if not _is_int(val) or not (val >= c.literal):
            return False
    elif c.op == '<':
        if not _is_int(val) or not (val < c.literal):
            return False
    elif c.op == '<=':
        if not _is_int(val) or not (val <= c.literal):
            return False
    else:
        return False
    return True


def evaluate_detail(clauses: Sequence[Clause], values: Dict[str, Any]) -> Tuple[bool, List[Clause]]:
    """求值并返回 (是否全部满足, 未满足的子句列表)。

    供路由决策可观测性使用：拒绝切换/结算时，能告诉玩家/作者
    「哪条子句未满足」，而非只给一个布尔结果。
    """
    unmet: List[Clause] = []
    for c in clauses:
        if not _clause_ok(c, values):
            unmet.append(c)
    return (not unmet, unmet)


def format_clauses(clauses: Sequence[Clause], values: Dict[str, Any]) -> str:
    """把子句列表格式化为人类可读的未满足原因，含当前实际值。

    例：`npc_trust >= 5（当前 npc_trust=3）`、`keys contains 档案室钥匙（当前 keys=["a"]）`。
    """
    parts: List[str] = []
    for c in clauses:
        cur = values.get(c.var)
        cur_s = repr(cur) if cur is not None else '（未设置）'
        if c.op == 'contains':
            parts.append(f'{c.var} contains {c.literal!r}（当前 {c.var}={cur_s}）')
        else:
            parts.append(f'{c.var} {c.op} {c.literal!r}（当前 {c.var}={cur_s}）')
    return '；'.join(parts) if parts else '（无）'


def parse_and_validate(expr: Optional[str], var_types: Dict[str, str]) -> List[Clause]:
    """解析并校验，供加载期一次性调用。"""
    clauses = parse(expr)
    validate(clauses, var_types)
    return clauses
