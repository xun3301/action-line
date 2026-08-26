"""游戏状态：玩家数据（GameState）与流程元状态（FlowState）分离。

玩家数据 GameState：剧本 variables 声明过的变量表，增量应用与类型校验。
流程元状态 FlowState：引擎流程自身的状态（当前章节、回合数、结局状态），
与剧情数据无关，可独立持久化/恢复。

变量类型：bool | int | str | str[]。
增量应用规则（见交接文档 §5.1）：
- 只接受剧本 variables 声明过的变量；
- 类型不符或未声明的字段丢弃并告警；
- str[] 为整体赋值（作者自己给出覆盖后的完整列表）。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

VALID_TYPES = ('bool', 'int', 'str', 'str[]')

DEFAULT_BY_TYPE: Dict[str, Any] = {
    'bool': False,
    'int': 0,
    'str': '',
    'str[]': [],
}


def type_ok(vtype: str, value: Any) -> bool:
    """判断 value 是否可视为给定变量类型。"""
    if vtype == 'bool':
        return isinstance(value, bool)
    if vtype == 'int':
        return isinstance(value, int) and not isinstance(value, bool)
    if vtype == 'str':
        return isinstance(value, str)
    if vtype == 'str[]':
        return isinstance(value, list) and all(isinstance(x, str) for x in value)
    return False


def _type_name(value: Any) -> str:
    if isinstance(value, bool):
        return 'bool'
    if isinstance(value, int):
        return 'int'
    if isinstance(value, str):
        return 'str'
    if isinstance(value, list):
        return 'list'
    return type(value).__name__


class GameState:
    """持有当前变量值；负责校验并应用模型给出的增量更新。"""

    def __init__(self, var_types: Dict[str, str], values: Dict[str, Any]):
        self.var_types = var_types
        self.values = values

    @classmethod
    def from_variables(cls, variables: List[Any]) -> 'GameState':
        """从剧本 variables 定义初始化（默认值）。"""
        var_types: Dict[str, str] = {}
        values: Dict[str, Any] = {}
        for v in variables:
            var_types[v.name] = v.type
            values[v.name] = copy.deepcopy(v.default)
        return cls(var_types, values)

    @classmethod
    def from_values(cls, variables: List[Any], saved: Dict[str, Any]) -> 'GameState':
        """从存档恢复；缺变量用默认值兜底，类型不符回退默认值。"""
        var_types: Dict[str, str] = {}
        values: Dict[str, Any] = {}
        for v in variables:
            var_types[v.name] = v.type
            val = saved.get(v.name, v.default) if isinstance(saved, dict) else v.default
            if not type_ok(v.type, val):
                val = copy.deepcopy(v.default)
            values[v.name] = val
        return cls(var_types, values)

    def apply_updates(self, updates: Any) -> List[str]:
        """应用 state_updates，返回告警文本列表（丢弃非法字段时）。"""
        warnings: List[str] = []
        if not isinstance(updates, dict):
            return warnings
        for name, value in updates.items():
            if name not in self.var_types:
                warnings.append(f'未声明变量 {name!r} 被丢弃（模型试图更新）')
                continue
            vtype = self.var_types[name]
            if not type_ok(vtype, value):
                warnings.append(
                    f'变量 {name!r} 类型不符（期望 {vtype}，收到 {_type_name(value)}），已丢弃'
                )
                continue
            self.values[name] = value
        return warnings


# 流程阶段（FlowState.phase 的取值）
PHASE_PLAYING = 'playing'      # 正常游玩
PHASE_CONFIRM = 'confirm'      # 命中结局待确认（confirm_ending）
PHASE_ENDED = 'ended'          # 已结算/已结束


@dataclass
class FlowState:
    """流程元状态：引擎流程自身的状态，与玩家数据 GameState 分离。

    对应外部评审建议「GraphState（当前步/是否结束）与玩家数据分离」：
    - 剧情数据（变量值）归 GameState；
    - 引擎流程（当前章节、回合计数、结局状态）归 FlowState。
    好处：流程状态可整体持久化/恢复、可独立观测（/status），
    不再散布在 Game 的各个局部属性里。
    """

    current_chapter: str = ''
    turn_count: int = 0
    ending: Any = None             # 已结算的 Ending 对象；None = 未结束
    pending_ending: Any = None     # confirm_ending 挂起、待玩家确认的 Ending
    max_turns: Optional[int] = None  # 整局回合上限（来自剧本 frontmatter，缺省不限）

    def to_dict(self) -> Dict[str, Any]:
        """序列化（存档用）。ending/pending_ending 只存 id，不存对象。"""
        return {
            'current_chapter': self.current_chapter,
            'turn_count': self.turn_count,
            'ending': getattr(self.ending, 'id', None),
            'pending_ending': getattr(self.pending_ending, 'id', None),
            'max_turns': self.max_turns,
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]],
                  default_chapter: str = '',
                  default_max_turns: Optional[int] = None) -> 'FlowState':
        """从存档恢复。缺字段回退默认；畸形字段丢弃。

        注意：ending / pending_ending 一律不恢复（读档即回到游玩态，
        与历史行为一致——待确认/已结算状态不跨存档延续）。
        """
        if not isinstance(data, dict):
            return cls(current_chapter=default_chapter, max_turns=default_max_turns)
        chapter = data.get('current_chapter') or default_chapter
        if not isinstance(chapter, str) or not chapter:
            chapter = default_chapter
        tc = data.get('turn_count')
        if isinstance(tc, bool) or not isinstance(tc, int) or tc < 0:
            tc = 0
        mt = data.get('max_turns')
        if isinstance(mt, bool) or not isinstance(mt, int) or mt < 1:
            mt = default_max_turns
        return cls(
            current_chapter=chapter,
            turn_count=tc,
            max_turns=mt,
        )

    @property
    def phase(self) -> str:
        """当前流程阶段：playing / confirm / ended。"""
        if self.ending is not None:
            return PHASE_ENDED
        if self.pending_ending is not None:
            return PHASE_CONFIRM
        return PHASE_PLAYING
