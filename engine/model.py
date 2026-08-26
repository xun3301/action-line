"""API 调用、指数退避重试、JSON 容错解析、失败降级分类。

失败恢复原则（见交接文档 §5.3）：退化而不是崩溃。
降级分类（对应外部评审建议「更细的失败降级分类」）：
- 超时 / 网络 / 5xx / 限流（429）：瞬时错误，指数退避重试；
- 内容过滤 / 认证 / 参数错误等 4xx：永久错误，不重试，抛 ModelError 并带 reason；
- JSON 输出格式错误：重试一次，仍失败则裸文本降级（degraded_reason='format'）。
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, Optional

import openai
from openai import OpenAI


class ModelError(Exception):
    """调用模型失败（网络/限流/内容过滤/永久错误等）。

    reason 字段标记失败类别，供上层给出不同玩家提示：
    'timeout' | 'content_filter' | 'permanent' | 'network'
    """

    def __init__(self, message: str, reason: str = 'network'):
        super().__init__(message)
        self.reason = reason


_PLACEHOLDER_KEYS = ('', 'sk-...', 'sk-')


def _is_permanent_error(exc) -> bool:
    """4xx（除 429 限流）为永久错误，不应重试；5xx/429/网络错误可重试。"""
    if isinstance(exc, openai.APIStatusError):
        return exc.status_code < 500 and exc.status_code != 429
    return False


def _is_response_format_error(exc) -> bool:
    """判断异常是否因端点不支持 response_format 导致。"""
    msg = str(exc).lower()
    return ('response_format' in msg) or ('response format' in msg)


def _is_content_filter_error(exc) -> bool:
    """判断异常是否因模型内容过滤（安全策略拒绝生成）导致。"""
    if isinstance(exc, openai.BadRequestError):
        msg = str(exc).lower()
        if 'content_filter' in msg or 'content filter' in msg:
            return True
        # openai SDK 的 message 也可能给出 finish_reason 信息
        try:
            body = exc.body
            if isinstance(body, dict):
                err = body.get('error') or {}
                if isinstance(err, dict) and 'content_filter' in str(err).lower():
                    return True
        except Exception:
            pass
    return False


def _classify_error(exc) -> str:
    """把异常归类为失败类别：timeout / content_filter / permanent / network。"""
    if isinstance(exc, openai.APITimeoutError):
        return 'timeout'
    if _is_content_filter_error(exc):
        return 'content_filter'
    if _is_permanent_error(exc):
        return 'permanent'
    return 'network'


def _strip_fence(s: str) -> str:
    s = s.strip()
    if s.startswith('```'):
        nl = s.find('\n')
        if nl != -1:
            s = s[nl + 1:]
        s = s.rstrip()
        if s.endswith('```'):
            s = s[:-3]
    return s.strip()


def _try_loads(s: str) -> Optional[dict]:
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    return None


def _repair(s: str) -> str:
    # 常见错误：尾逗号、缺引号的键
    s = re.sub(r',\s*\}', '}', s)
    s = re.sub(r',\s*\]', ']', s)
    s = re.sub(r'([{,])\s*([A-Za-z_][A-Za-z0-9_]*)\s*:', r'\1"\2":', s)
    return s


def extract_json(text: Optional[str]) -> Optional[dict]:
    """容错提取 JSON 对象：剥离代码围栏、截取首个完整 JSON 块、修复常见错误。"""
    if not text:
        return None
    s = _strip_fence(text)
    obj = _try_loads(s)
    if obj is not None:
        return obj
    start = s.find('{')
    if start == -1:
        return None
    decoder = json.JSONDecoder()
    i = start
    while i < len(s):
        if s[i] == '{':
            try:
                obj, _end = decoder.raw_decode(s[i:])
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                pass
        i += 1
    end = s.rfind('}')
    if end == -1:
        return None
    return _try_loads(_repair(s[start:end + 1]))


def _first_sentence(text: str) -> str:
    if not text:
        return '（无）'
    m = re.split(r'(?<=[。！？.!?])', text, maxsplit=1)
    first = m[0].strip()
    if len(first) > 60:
        first = first[:60] + '…'
    return first or '（无）'


def _normalize(env: dict) -> Dict[str, Any]:
    result = {
        'narrative': '',
        'state_updates': {},
        'chapter_proposal': None,
        'ending': None,
        'event_log': '',
        'degraded': False,
    }
    narrative = env.get('narrative')
    if not isinstance(narrative, str):
        narrative = ''
    result['narrative'] = narrative.strip()

    su = env.get('state_updates')
    if isinstance(su, dict):
        result['state_updates'] = su

    cp = env.get('chapter_proposal')
    if isinstance(cp, str) and cp.strip():
        result['chapter_proposal'] = cp.strip()

    e = env.get('ending')
    if isinstance(e, str) and e.strip():
        result['ending'] = e.strip()

    el = env.get('event_log')
    if not isinstance(el, str) or not el.strip():
        el = _first_sentence(narrative)
    result['event_log'] = el.strip()
    return result


def _degrade(raw: str, reason: str = 'format') -> Dict[str, Any]:
    text = (raw or '').strip() or '（模型未返回内容）'
    return {
        'narrative': text,
        'state_updates': {},
        'chapter_proposal': None,
        'ending': None,
        'event_log': _first_sentence(text),
        'degraded': True,
        'degraded_reason': reason,
    }


class ModelClient:
    def __init__(self, cfg):
        self.cfg = cfg
        self.model = cfg['model']
        self.json_mode = cfg['json_mode']
        self.retries = 3
        self._json_unsupported = False
        self.client = None
        self.key_missing = None
        key = (cfg.get('api_key') or '').strip()
        if key in _PLACEHOLDER_KEYS:
            self.key_missing = key or '<empty>'
        else:
            self.client = OpenAI(base_url=cfg['base_url'], api_key=key, timeout=120.0)

    def _request(self, messages, **extra):
        resp = self.client.chat.completions.create(
            model=self.model, messages=messages, **extra
        )
        return (resp.choices[0].message.content) or ''

    def _single(self, system, user):
        messages = [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user},
        ]
        if self.json_mode and not self._json_unsupported:
            try:
                return self._request(messages, response_format={'type': 'json_object'})
            except Exception as e:
                if _is_response_format_error(e):
                    # 端点不支持 response_format：缓存标记，后续直接走普通模式
                    self._json_unsupported = True
                    return self._request(messages)
                raise
        return self._request(messages)

    def _chat(self, system, user):
        if self.client is None:
            raise ModelError(
                '未配置有效的 api_key，请编辑 config.toml 填入真实密钥'
                '（当前为 ' + repr(self.key_missing) + '）',
                reason='permanent',
            )
        last = None
        last_reason = 'network'
        for attempt in range(self.retries):
            try:
                return self._single(system, user)
            except ModelError:
                raise
            except Exception as e:
                last_reason = _classify_error(e)
                if last_reason in ('permanent', 'content_filter'):
                    raise ModelError(
                        '调用模型失败（永久错误，未重试）：' + str(e),
                        reason=last_reason,
                    )
                last = e
                if attempt < self.retries - 1:
                    time.sleep(2 ** attempt)
        raise ModelError(
            '调用模型失败（已重试 ' + str(self.retries) + ' 次）：' + str(last),
            reason=last_reason,
        )

    def call(self, system_prompt, user_input):
        raw = self._chat(system_prompt, user_input)
        env = extract_json(raw)
        if env is None:
            hint = system_prompt + '\n\n【系统提示】你上一次的输出未能解析为合法 JSON。请务必只输出一个合法 JSON 信封，不要输出任何 JSON 以外的内容。'
            raw2 = self._chat(hint, user_input)
            env = extract_json(raw2)
            if env is None:
                return _degrade(raw2 or raw, reason='format')
        return _normalize(env)
