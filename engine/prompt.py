"""系统 prompt 组装：分段披露 + event_log 摘要 + 最近 K 轮原文。

顺序固定（见交接文档 §6.1）：
角色指令 -> 常驻区 -> 章节梗概表 -> 结局清单 -> 关键字面量 -> 当前章节全文 ->
当前状态 -> 行为准则 -> 已发生事件摘要 -> 最近 K 轮原文 -> 输出格式要求。

结局清单与关键字面量来自 frontmatter（非章节【内部指示】），从首章起全量注入，
使模型始终知道合法结局 id 与精确匹配字面量，无需猜测。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Sequence

# 注入预算（按字符粗估，约对应 8k token 的中文语境）
BUDGET_CHARS = 16000

_ROLE_TMPL = (
    '你是文字互动游戏《{title}》的叙述者。'
    '依据剧本、当前状态与玩家输入推进剧情，并驱动状态、章节与结局的演进。'
    '你只能依据下方提供的「常驻区」「章节梗概」「当前章节全文」推进，不得编造未披露的人物、地点或事件。'
)

_OUTPUT_FORMAT = (
    '【输出格式要求】\n'
    '你必须只输出一个合法的 JSON 对象（不要用 markdown 代码围栏包裹），结构如下：\n'
    '{\n'
    '  "narrative": "给玩家的叙事正文，可含 NPC 对白，Markdown 可选",\n'
    '  "state_updates": {"变量名": 新值},\n'
    '  "chapter_proposal": "章节id 或 null",\n'
    '  "ending": "结局id 或 null",\n'
    '  "event_log": "本回合 1-2 句话的事态摘要"\n'
    '}\n'
    '字段规则：\n'
    '- narrative：必填，是唯一直接展示给玩家的内容。NPC 对白请用 *斜体*（一对星号）标记。\n'
    '- state_updates：增量更新，只允许修改剧本 variables 声明过的变量；str[] 为整体赋值——'
    '每回合若更新，必须给出覆盖后的完整列表，未变化的已有元素也必须逐字重列，否则会丢失。\n'
    '- chapter_proposal：仅当剧情确实需要切换章节时才给章节 id，否则为 null。\n'
    '- ending：只能从【结局清单】中选择 id，且仅当你判断其条件已满足时才声明，否则为 null。\n'
    '- event_log：必填，用 1-2 句话概括本回合关键进展，供长期记忆使用。\n'
    '- 若当前章节全文里出现【内部指示】段落，那是给叙述者的操作提示，不要照搬进 narrative。\n'
)


def _section(title, body):
    return '【' + title + '】\n' + body


def _summaries(script) -> str:
    lines = []
    for ch in script.chapters:
        lines.append('- ' + ch.id + '（' + ch.title + '）：' + ch.summary)
    return '\n'.join(lines)


def _endings(script) -> str:
    """结局清单：合法 id + 原始 when 表达式，供模型判断与声明。"""
    lines = []
    for e in script.endings:
        tag = '（自动触发，由引擎结算，你无需声明）' if e.auto else ''
        lines.append('- ' + e.id + '（' + e.name + '）' + tag + '：' + e.when_expr)
    return '\n'.join(lines)


_RULES = (
    '- 【内部指示】是必须遵守的硬性规则，其优先级高于你的创作自由；冲突时以【内部指示】为准。\n'
    '- 状态一致：你在 narrative 中描述的任何数值（信号格数、剩余时间、信任高低、是否持有某物品）'
    '必须与【当前状态】及你本轮 state_updates 修改后的结果一致，不得自相矛盾。\n'
    '- 连贯性：已经发生过的关键场景（如播放某段录音、找到某件物品、某人说出某句关键台词）'
    '不得当作全新场景逐字重演；如需再次提及，必须明确是「回放 / 回忆 / 回顾」，且要简短，不要重复完整原文。\n'
    '- 后果如实：当你把某个变量改到会触发结局的临界（如时间归零、信任崩塌），narrative 必须如实呈现该后果，'
    '不要继续描绘相反方向（例如时间已尽却仍写「信号清晰」）。\n'
    '- 结局触发：标注「自动触发」的结局由引擎自行结算，你**无需**在 ending 字段声明；'
    '仅当剧本存在未标注「自动触发」的结局时，若玩家做出明确的最终抉择且该结局条件已满足，'
    '你才需要在 ending 字段声明其 id，不要只写叙事而不声明。\n'
    '- 除非有明确理由，不要引入剧本未披露的新人物、新地点或新机制；如需扩展，须与已有设定自洽。'
)


def _key_literals(script) -> str:
    """从结局/章节衔接条件的已解析子句中提取字符串字面量（自动收集，精确匹配值）。

    这些值是条件求值的精确匹配目标（如物品名、地点枚举），模型写入
    state_updates 时必须逐字使用，可降低拼写漂移导致的条件永不达成。
    """
    seen = []
    clauses_list = [e.when for e in script.endings]
    clauses_list += [c.when for c in script.chapters if c.when]
    for clauses in clauses_list:
        for cl in clauses or []:
            lit = getattr(cl, 'literal', None)
            if isinstance(lit, str) and lit not in seen:
                seen.append(lit)
    if not seen:
        return '（无）'
    return '、'.join('「' + x + '」' for x in seen)


def _render_history(h) -> str:
    if not isinstance(h, dict):
        return ''
    user = h.get('user') if isinstance(h.get('user'), str) else ''
    narrative = h.get('narrative') if isinstance(h.get('narrative'), str) else ''
    return '玩家：' + user + '\n你：' + narrative


def _render_logs(logs: Sequence[str]) -> str:
    if not logs:
        return '（暂无）'
    return '\n'.join('- ' + x for x in logs)


def build_system_prompt(script, state_values, current_chapter_id,
                        event_logs, history) -> str:
    ch = script.chapter_by_id[current_chapter_id]
    head = [
        _ROLE_TMPL.format(title=script.title),
        _section('常驻世界观', script.constant_world or '（无）'),
        _section('常驻人物设定', script.constant_characters or '（无）'),
        _section('章节梗概', _summaries(script)),
        _section('结局清单', '本剧本的合法结局如下，ending 字段只能使用其中的 id：\n' + _endings(script)),
        _section('关键字面量',
                 '以下字符串值在条件求值中为精确匹配，写入 state_updates 时必须逐字使用，'
                 '不得改写、翻译或换成同义词：\n' + _key_literals(script)),
        _section('当前章节全文（' + current_chapter_id + '）', ch.body or '（空）'),
        _section('当前状态', json.dumps(state_values, ensure_ascii=False, indent=2)),
        _section('行为准则', _RULES),
    ]

    core_len = sum(len(p) for p in head) + len(_OUTPUT_FORMAT)
    remaining = max(0, BUDGET_CHARS - core_len)

    # 原文：新→旧放入，先丢更早的轮次
    kept_hist = []
    for h in reversed(history):
        text = _render_history(h)
        if len(text) <= remaining:
            kept_hist.append(text)
            remaining -= len(text)
        else:
            break
    kept_hist.reverse()

    # 摘要：新→旧放入，其次丢更早的 event_log
    kept_logs = []
    for lg in reversed(event_logs):
        cost = len(lg) + 2
        if cost <= remaining:
            kept_logs.append(lg)
            remaining -= cost
        else:
            break
    kept_logs.reverse()

    head.append(_section('已发生事件摘要', _render_logs(kept_logs)))
    head.append(_section('最近对话', '\n\n'.join(kept_hist) or '（暂无）'))
    head.append(_OUTPUT_FORMAT)
    return '\n\n'.join(head)
