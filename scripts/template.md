---
# ===== StoryShell 剧本骨架（模板） =====
# 复制本文件到 scripts/ 目录并改名（不要叫 template.md）即可开始写作。
# frontmatter 为 YAML，六必填字段：title / intro / constant / chapters / variables / endings。

title: 我的剧本                 # 剧本名（必填）
intro: 这是开场白，游戏开始时展示。
# max_turns: 30               # 可选：整局回合上限（正整数）；省略 = 不限
# confirm_ending: true        # 可选：命中结局后先挂起，需玩家 /end 确认才结束；省略 = false

constant:                      # 常驻区：永远全量披露
  world: 世界观常量（时代、地点、核心矛盾）
  characters: 核心NPC设定（跨章节出现的角色放这里）

chapters:                      # 章节梗概表，按顺序披露（必填，非空）
  - id: ch1                    # 章节 id：须与正文「## 章节: ch1」一致，且唯一
    title: 第一章
    summary: 本章梗概，1-3句（只透露本章相关）
    # when: "npc_trust >= 2"   # 可选：切入本章需满足的衔接条件（语法同 ending 的 when）
  - id: ch2
    title: 第二章
    summary: 本章梗概

variables:                     # 状态变量表：唯一合法状态来源（必填）
  - name: keys                 # 变量名：合法标识符，唯一
    type: str[]                # bool | int | str | str[]
    default: []                # 默认值（须与 type 匹配）
  - name: npc_trust
    type: int
    default: 0
  - name: met_alan
    type: bool
    default: false
  - name: diary_location
    type: str
    default: ""

endings:                       # 结局表（必填，非空）
  - id: ending_good            # 结局 id：唯一
    name: 好结局               # 展示名
    when: "npc_trust >= 5 && keys contains 档案室钥匙"   # 触发条件（必填）
    # auto: true               # 可选：true = 条件满足即由引擎自动结算（无需模型声明），触发即结束
    # priority: 100            # 可选：自动结局的结算优先级（整数，越大越优先；仅对 auto:true 有意义）
  - id: ending_bad             # 失败/超时类结局建议设为 auto:true + 高 priority，
    name: 坏结局               # 使「时间到/信任崩/死亡」等强制结局由引擎确定性触发
    when: "npc_trust <= -3"
    auto: true
    priority: 100
---

## 章节: ch1

这里是第一章全文：环境描写、NPC 当前状态、可交互物品等，都属于章节全文。
NPC 对白用 *一对星号* 包裹，渲染为斜体。

【内部指示】可选：给叙述者的操作提示，不要照搬给玩家。例如：
当玩家礼貌询问时，通过 state_updates 设置 {"npc_trust": 1}。

---

# ===== 写作最佳实践（贴合模型与引擎） =====

1. **str 变量只做枚举**：凡进入 `when` 条件比较的 str 变量（如地点、状态），在【内部指示】里
   显式列出全部合法值（如：只能填 ""、"地点A"、"地点B"）。避免让模型自由发挥写成叙事性文字。
2. **str[] 物品名要短而精确**：物品字面量只设少数几个，并在各章【内部指示】逐字重复，提醒
   "整体赋值、逐字重列"。引擎会自动把 `when` 里出现的字符串字面量注入 prompt，别用同义词。
3. **所有结局一律用 auto:true**：实测证明"让模型主动声明结局"不可靠（条件满足时模型常不声明）。
   失败/超时/关系崩盘等"状态已终结"结局交给引擎自动结算（触发即结束）；用 priority 区分同时满足时的先后。
4. **好结局用"决策变量"收口**：当多个好结局可能同时满足时，加一个 str 决策变量（如 decision），
   让各结局的 `when` 以 `decision == ...` 互相排斥，同样设 auto:true。玩家做出最终抉择时，模型只需
   在 state_updates 里把 decision 设为对应枚举值，引擎即自动结算——完全摆脱"模型是否记得声明结局"。
5. **倒计时只放在终章**：时间压力类变量（如 time_left）只在最后一章递减，避免"调查还没做完就
   超时"的尴尬；调查阶段不扣时。
6. **数字变量在叙事中保持同步**：若【内部指示】要求改某数值，务必同时提醒模型"narrative 中
   对该数值的描述必须与修改后的值一致"。
7. **章节 when 门控**：用 `when` 让引擎参与节奏裁判（如信任够才进下一章），比全靠模型自觉更稳。
8. **枚举值在首章就绑定**：决策变量、地点等 str 枚举的合法值，在 ch1【内部指示】就写清楚
   （引擎会自动把 `when` 里的字面量注入 prompt，但"变量→值"的绑定需在指示里点明）。

## 章节: ch2

这里是第二章全文。
