"""回合主循环、章节推进、结局结算、斜杠命令。"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from rich import box
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import IntPrompt, Prompt
from rich.text import Text

from . import condition
from . import save as save_mod
from .model import ModelClient, ModelError
from .prompt import build_system_prompt
from .state import GameState, FlowState

console = Console()

STYLE_SYSTEM = 'bold cyan'
STYLE_WARN = 'bold yellow'
STYLE_ERROR = 'bold red'
STYLE_SUCCESS = 'bold green'


def _system(text: str, style: str = STYLE_SYSTEM) -> None:
    console.print(Panel(Text(text, style=style), border_style=style, box=box.ROUNDED))


def _valid_history(history) -> List[Dict[str, str]]:
    """校验并清洗存档中的历史条目，丢弃畸形项。"""
    if not isinstance(history, list):
        return []
    out = []
    for h in history:
        if isinstance(h, dict) and isinstance(h.get('user'), str) and isinstance(h.get('narrative'), str):
            out.append({'user': h['user'], 'narrative': h['narrative']})
    return out


def _valid_logs(logs) -> List[str]:
    if not isinstance(logs, list):
        return []
    return [str(x) for x in logs]


def _model_error_text(e: ModelError) -> str:
    """按失败类别给出不同的玩家提示（更细的失败降级分类）。"""
    prefix = {
        'timeout': '[超时] 模型响应超时',
        'content_filter': '[内容被拒] 模型因安全策略拒绝了本次生成',
        'permanent': '[错误] 调用模型失败（认证/参数等永久错误）',
        'network': '[网络错误] 调用模型失败',
    }.get(e.reason, '[错误] 调用模型失败')
    return prefix + '：' + str(e)


def _degraded_text(reason: str) -> str:
    """模型输出降级时的分类提示。"""
    if reason == 'format':
        return '模型输出未能解析为 JSON，已按原文直接展示（本回合状态未更新）。'
    if reason == 'timeout':
        return '模型响应超时，已按原文直接展示（本回合状态未更新）。'
    if reason == 'content_filter':
        return '模型因安全策略拒绝生成，已按原文直接展示（本回合状态未更新）。'
    return '模型输出异常，已按原文直接展示（本回合状态未更新）。'


class Game:
    def __init__(self, cfg, script):
        self.cfg = cfg
        self.script = script
        # 玩家数据与流程元状态分离：剧情变量归 GameState，引擎流程归 FlowState
        self.state = GameState.from_variables(script.variables)
        self.flow = FlowState(
            current_chapter=script.chapters[0].id,
            max_turns=script.max_turns,
        )
        self.event_logs: List[str] = []
        self.history: List[Dict[str, str]] = []
        self.client = ModelClient(cfg)
        self._chapter_order = {c.id: i for i, c in enumerate(script.chapters)}

    # ---- 流程元状态代理属性（内部统一走 self.flow，此处仅为兼容旧访问方式） ----
    @property
    def current_chapter(self) -> str:
        return self.flow.current_chapter

    @current_chapter.setter
    def current_chapter(self, v: str) -> None:
        self.flow.current_chapter = v

    @property
    def turn_count(self) -> int:
        return self.flow.turn_count

    @turn_count.setter
    def turn_count(self, v: int) -> None:
        self.flow.turn_count = v

    @property
    def ending(self):
        return self.flow.ending

    @ending.setter
    def ending(self, v) -> None:
        self.flow.ending = v

    @property
    def pending_ending(self):
        return self.flow.pending_ending

    @pending_ending.setter
    def pending_ending(self, v) -> None:
        self.flow.pending_ending = v

    @property
    def max_turns(self):
        return self.flow.max_turns

    @max_turns.setter
    def max_turns(self, v) -> None:
        self.flow.max_turns = v

    # ---- 主流程 ----
    def run(self):
        console.print(Markdown('# ' + self.script.title))
        if self.script.intro:
            console.print(Markdown(self.script.intro))
        _system('直接输入文字即可行动或对话；输入 /help 查看命令。')

        while self.flow.ending is None:
            if self.flow.max_turns is not None and self.flow.turn_count >= self.flow.max_turns:
                _system('已达到剧本设定的回合上限（' + str(self.flow.max_turns) + '），本局结束。', STYLE_WARN)
                break
            try:
                user = Prompt.ask('> ')
            except (EOFError, KeyboardInterrupt):
                console.print()
                break
            user = user.strip()
            if not user:
                continue
            if user.startswith('/'):
                if self._command(user):
                    break
                continue
            # 处于待确认结局状态时，玩家输入任意非命令内容 = 选择继续游戏，取消挂起
            if self.flow.pending_ending is not None:
                self.flow.pending_ending = None
                _system('已取消结局确认，继续游戏。', STYLE_WARN)
            try:
                self._turn(user)
            except ModelError as e:
                _system(_model_error_text(e) + '\n已保留你的输入，回车重试。', STYLE_ERROR)
                continue
            except Exception as e:
                _system('[意外错误] ' + repr(e) + '\n已保留你的输入与进度，可 /save 存档或 /quit 退出。', STYLE_ERROR)
                continue

        if self.flow.ending is not None:
            _system('—— 结局达成：' + self.flow.ending.name + ' ——', STYLE_SUCCESS)
        console.print('感谢游玩。')

    def _turn(self, user):
        system_prompt = build_system_prompt(
            self.script,
            self.state.values,
            self.flow.current_chapter,
            self.event_logs,
            self.history[-self.cfg['history_rounds']:],
        )
        result = self.client.call(system_prompt, user)
        narrative = result['narrative']

        notices = []  # (text, style)
        if result.get('degraded'):
            notices.append((_degraded_text(result.get('degraded_reason', 'format')), STYLE_WARN))
        for w in self.state.apply_updates(result['state_updates']):
            notices.append((w, STYLE_WARN))

        proposal = result['chapter_proposal']
        if proposal and proposal != self.flow.current_chapter:
            self._handle_chapter(proposal, notices)

        # 自动结局优先：状态满足即结算（引擎是最终裁判，不依赖模型声明）
        auto = self._check_auto_endings()
        target = auto
        if target is None and result['ending']:
            target = self._resolve_ending(result['ending'], notices)
        if target is not None:
            self._settle(target, notices)

        console.print()
        console.print(Markdown(narrative))
        console.print()
        for text, style in notices:
            _system(text, style)

        self.history.append({'user': user, 'narrative': narrative})
        self.event_logs.append(result['event_log'])
        self.flow.turn_count += 1

    def _handle_chapter(self, proposal, notices):
        ch = self.script.chapter_by_id.get(proposal)
        if ch is None:
            notices.append(('模型建议切换到的章节 ' + repr(proposal) + ' 不存在，已忽略', STYLE_WARN))
            return
        # 线性推进：拒绝回退到已历章节
        cur_i = self._chapter_order.get(self.flow.current_chapter, -1)
        prop_i = self._chapter_order.get(proposal, -1)
        if prop_i <= cur_i:
            notices.append(('模型建议回退到章节「' + ch.title + '」，线性推进已拒绝', STYLE_WARN))
            return
        if ch.when is not None:
            ok, unmet = condition.evaluate_detail(ch.when, self.state.values)
            if not ok:
                detail = condition.format_clauses(unmet, self.state.values)
                notices.append(('章节切换至「' + ch.title + '」被拒绝：衔接条件不满足\n未满足：' + detail, STYLE_WARN))
                return
        self.flow.current_chapter = ch.id
        notices.append(('[章节推进] → ' + ch.title, STYLE_SUCCESS))

    def _resolve_ending(self, eid, notices):
        """校验模型声明的结局，命中则返回 Ending 对象，否则返回 None。"""
        e = self.script.ending_by_id.get(eid)
        if e is None:
            notices.append(('模型声明的结局 ' + repr(eid) + ' 不存在，已忽略', STYLE_WARN))
            return None
        ok, unmet = condition.evaluate_detail(e.when, self.state.values)
        if ok:
            return e
        detail = condition.format_clauses(unmet, self.state.values)
        notices.append(('结局「' + e.name + '」条件不满足，无法结算，游戏继续\n未满足：' + detail, STYLE_WARN))
        return None

    def _settle(self, ending, notices):
        """结算一个已命中的结局：默认立即结束；开启 confirm_ending 则先挂起等待确认。"""
        if self.script.confirm_ending:
            self.flow.pending_ending = ending
            notices.append((
                '结局「' + ending.name + '」条件已满足。输入 /end 确认结束，或继续输入其它内容以继续游戏。',
                STYLE_SUCCESS
            ))
        else:
            self.flow.ending = ending

    def _check_auto_endings(self):
        """返回当前状态满足的、优先级最高的自动结局；无则返回 None。"""
        for e in self.script.auto_endings:
            if condition.evaluate(e.when, self.state.values):
                return e
        return None

    # ---- 斜杠命令 ----
    def _command(self, user):
        parts = user.split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ''
        if cmd == '/help':
            _system(
                '命令：\n/save [名字]  存档（默认时间戳名）\n/load  列出存档供选择\n'
                '/status  查看状态\n/end  确认结束（仅当结局待确认时有效）\n/quit  退出'
            )
        elif cmd == '/save':
            self._save(arg)
        elif cmd == '/load':
            self._load()
        elif cmd == '/status':
            self._status()
        elif cmd == '/end':
            if self.flow.pending_ending is not None:
                self.flow.ending = self.flow.pending_ending
                self.flow.pending_ending = None
                return True
            _system('当前没有待确认的结局。', STYLE_WARN)
        elif cmd == '/quit':
            return True
        else:
            _system('未知命令 ' + cmd + '。输入 /help 查看可用命令。', STYLE_WARN)
        return False

    def _save(self, name):
        data = {
            'script_title': self.script.title,
            'chapter': self.flow.current_chapter,
            'variables': self.state.values,
            'event_logs': self.event_logs,
            'history': self.history[-self.cfg['history_rounds']:],
            'flow': self.flow.to_dict(),  # 流程元状态：回合数等随存档持久化
        }
        path = save_mod.write_save(
            self.cfg['saves_dir'], self.script.title, data, self.cfg['max_saves'],
            name=name or None,
        )
        _system('已存档：' + os.path.basename(path) + '\n路径：' + path, STYLE_SUCCESS)

    def _load(self):
        saves = save_mod.list_saves(self.cfg['saves_dir'], self.script.title)
        if not saves:
            _system('暂无存档。', STYLE_WARN)
            return
        console.print('选择存档：')
        for i, p in enumerate(saves, 1):
            console.print('  [' + str(i) + '] ' + os.path.basename(p), markup=False)
        console.print('  [0] 取消', markup=False)
        try:
            choice = IntPrompt.ask('> ', default=0)
        except Exception:
            return
        if choice <= 0 or choice > len(saves):
            return
        self._load_path(saves[choice - 1])

    def _load_path(self, path):
        try:
            data = save_mod.read_save(path)
        except save_mod.SaveError as e:
            _system('[错误] ' + str(e), STYLE_ERROR)
            baks = save_mod.list_backups(self.cfg['saves_dir'], self.script.title)
            if baks:
                _system('可用备份（.bak）：\n' + '\n'.join('  - ' + b for b in baks), STYLE_WARN)
            return
        title = data.get('script_title')
        if isinstance(title, str) and title and title != self.script.title:
            _system('[错误] 存档属于剧本「' + title + '」，与当前剧本「' + self.script.title + '」不符，已取消读档。', STYLE_ERROR)
            return

        # 流程元状态恢复：优先读 flow 块（含回合数）；旧档（无 flow）回退到 chapter 字段
        flow_data = data.get('flow')
        if isinstance(flow_data, dict):
            self.flow = FlowState.from_dict(
                flow_data,
                default_chapter=self.script.chapters[0].id,
                default_max_turns=self.script.max_turns,
            )
        else:
            self.flow = FlowState(
                current_chapter=(
                    data.get('chapter') if isinstance(data.get('chapter'), str)
                    and data['chapter'] in self.script.chapter_by_id
                    else self.script.chapters[0].id
                ),
                max_turns=self.script.max_turns,
            )
        if self.flow.current_chapter not in self.script.chapter_by_id:
            self.flow.current_chapter = self.script.chapters[0].id
        self.state = GameState.from_values(self.script.variables, data.get('variables') or {})
        self.event_logs = _valid_logs(data.get('event_logs'))
        self.history = _valid_history(data.get('history'))
        # 读档即回到游玩态：结算/待确认状态不跨存档延续
        self.flow.ending = None
        self.flow.pending_ending = None
        _system('读档成功：' + os.path.basename(path), STYLE_SUCCESS)

    def _status(self):
        ch = self.script.chapter_by_id[self.flow.current_chapter]
        lines = ['章节：' + ch.title + '（' + self.flow.current_chapter + '）', '状态变量：']
        for name, val in self.state.values.items():
            lines.append('  - ' + name + ' = ' + json.dumps(val, ensure_ascii=False))
        if self.flow.max_turns is not None:
            lines.append('回合：' + str(self.flow.turn_count) + ' / ' + str(self.flow.max_turns))
        elif self.flow.turn_count > 0:
            lines.append('回合：' + str(self.flow.turn_count))
        if self.flow.pending_ending is not None:
            lines.append('待确认结局：' + self.flow.pending_ending.name + '（输入 /end 结束，或继续输入以继续）')
        lines.append('结局条件满足度：')
        for e in self.script.endings:
            ok, unmet = condition.evaluate_detail(e.when, self.state.values)
            tag = '（自动）' if e.auto else ''
            state = '满足' if ok else ('未满足：' + condition.format_clauses(unmet, self.state.values))
            lines.append('  - ' + e.name + '（' + e.id + '）' + tag + '：' + state)
        _system('\n'.join(lines))
