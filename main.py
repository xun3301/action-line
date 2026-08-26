"""入口：剧本选择菜单 → 回合循环。"""

from __future__ import annotations

import os
import sys

from rich.console import Console
from rich.prompt import Prompt

from engine.game import Game
from engine.script import ScriptError, load_script

console = Console()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _force_utf8():
    # Windows 终端乱码防护
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            try:
                stream.reconfigure(encoding='utf-8')
            except Exception:
                pass


def load_config(path):
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore
    with open(path, 'rb') as f:
        data = tomllib.load(f)
    api = data.get('api', {})
    game = data.get('game', {})
    return {
        'base_url': api.get('base_url', 'https://api.deepseek.com/v1'),
        'api_key': (api.get('api_key') or '').strip(),
        'model': api.get('model', 'deepseek-chat'),
        'json_mode': bool(game.get('json_mode', False)),
        'history_rounds': int(game.get('history_rounds', 12)),
        'max_saves': int(game.get('max_saves', 20)),
        'scripts_dir': game.get('scripts_dir', 'scripts'),
        'saves_dir': game.get('saves_dir', 'saves'),
    }


def _resolve(path):
    if os.path.isabs(path):
        return path
    return os.path.join(BASE_DIR, path)


def scan_scripts(scripts_dir):
    if not os.path.isdir(scripts_dir):
        return []
    out = []
    for f in sorted(os.listdir(scripts_dir)):
        if f.endswith('.md') and f != 'template.md' and not f.startswith('_'):
            out.append(os.path.join(scripts_dir, f))
    return out


def main():
    _force_utf8()
    cfg = load_config(os.path.join(BASE_DIR, 'config.toml'))
    cfg['scripts_dir'] = _resolve(cfg['scripts_dir'])
    cfg['saves_dir'] = _resolve(cfg.get('saves_dir', 'saves'))

    if cfg['api_key'] in ('', 'sk-...', 'sk-'):
        console.print('[bold yellow]警告：config.toml 未配置有效的 api_key，模型调用将失败。[/]')

    scripts = scan_scripts(cfg['scripts_dir'])
    if not scripts:
        console.print('[red]在 ' + cfg['scripts_dir'] + ' 未找到任何可玩剧本（.md 文件）。[/]')
        return

    console.print('[bold]可用剧本：[/]')
    for i, p in enumerate(scripts, 1):
        console.print('  [' + str(i) + '] ' + os.path.basename(p)[:-3], markup=False)
    console.print('  [q] 退出', markup=False)

    choice = Prompt.ask('选择剧本编号')
    if choice.strip().lower() in ('q', 'quit', 'exit', ''):
        return
    try:
        idx = int(choice) - 1
    except ValueError:
        console.print('[red]输入无效。[/]')
        return
    if idx < 0 or idx >= len(scripts):
        console.print('[red]编号无效。[/]')
        return

    path = scripts[idx]
    try:
        script = load_script(path)
    except ScriptError as e:
        console.print('[red]剧本加载失败：' + str(e) + '[/]')
        return
    for w in script.warnings:
        console.print('[yellow]警告：' + w + '[/]')

    Game(cfg, script).run()


if __name__ == '__main__':
    main()
