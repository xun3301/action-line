---
title: 星海旅馆
intro: >
  你是一名星际调查员，受委托重查十年前的失踪悬案。
  你的旧飞船刚在边境空港"雾港"降落，雨雾弥漫。
  你推开"星海旅馆"那扇吱呀作响的门，门铃叮咚一响……

constant:
  world: >
    时代：近未来。地点：边境空港"雾港"一家名为"星海旅馆"的老旧旅馆。
    核心矛盾：旅馆即将被星际财团"赫利俄斯"收购拆除，而十年前旅馆主人之女"小满"的失踪案真相至今未明。
    你此行正是受委托重查此案。
  characters: |
    林姨：旅馆老板娘，小满的母亲，慈祥却守口如瓶，对陌生人谨慎。（跨章出现）
    阿岚：神秘长住客，行踪不定，似乎知道当年内情。（跨章出现）
    老周：旅馆的老门房，胆小但心软。（跨章出现）

chapters:
  - id: ch1
    title: 夜航入港
    summary: 你入住旅馆，向林姨了解情况，结识门房老周，察觉旅馆里有不寻常的动静。
  - id: ch2
    title: 尘封的走廊
    summary: 你深入旅馆调查，寻找小满失踪的线索，逐渐取得林姨的信任，并接触神秘住客阿岚。
  - id: ch3
    title: 真相之门
    summary: 线索汇聚，财团清场在即。你必须在时限内揭开谜底，面对最终抉择。

variables:
  - name: keys
    type: str[]
    default: []
  - name: npc_trust
    type: int
    default: 0
  - name: met_alan
    type: bool
    default: false
  - name: diary_location
    type: str
    default: ""

endings:
  - id: ending_truth
    name: 真相大白
    when: "npc_trust >= 5 && keys contains 档案室钥匙 && met_alan == true"
  - id: ending_escape
    name: 悄然离去
    when: "keys contains 后门钥匙 && diary_location == 地下室"
  - id: ending_lost
    name: 迷失星海
    when: "npc_trust <= -3"
    auto: true

---

## 章节: ch1

雨夜的大堂昏暗而安静。前台后的林姨抬头看你，眼神里带着长途旅人特有的警惕。她身后的墙上挂着一张褪色的合影，右下角缺了一角。

老周正在门口擦那扇永远关不严的门，见你进来，局促地笑了笑。

【内部指示】玩家可以向林姨询问案情、登记入住，或向老周打听。若玩家礼貌地说明来意并表达诚意，通过 state_updates 将 npc_trust 在现有基础上加 1；若玩家言行粗鲁、威胁或明显敌对，可酌情将 npc_trust 减分，允许扣为负值（信任过低会导向特定结局）。本阶段不要给钥匙，也不要透露阿岚的身份与去向。

## 章节: ch2

旅馆的走廊很长，两侧房门紧闭，墙纸卷起边角。空气中弥漫着一股旧木头和消毒水的味道。走廊尽头，一个人影靠在窗边——是阿岚，正用一块绒布擦着一只打火机，火光在他指间一明一灭。

你想起林姨提过，档案室和地下室都上了锁。老周也许知道钥匙的下落，但他似乎更怕林姨知道些什么。

【内部指示】本阶段可推进以下线索：
- 与阿岚交谈并取得他的信任：通过 state_updates 设置 {"met_alan": true}。
- 帮林姨整理旧物、倾听她谈起小满：逐步提升 npc_trust（每次在现有基础上加 1 到 2）。
- 若玩家找到小满的日记，通过 state_updates 设置 {"diary_location": "地下室"}。
- 若玩家从老周或林姨处取得钥匙，通过 state_updates 更新 keys（str[] 为整体赋值，例如 {"keys": ["档案室钥匙"]} 或 {"keys": ["后门钥匙"]}）。

## 章节: ch3

赫利俄斯财团的清场通告已经贴到了旅馆门口，限期就在天亮。林姨终于松口，老周也把藏了十年的东西交了出来。所有线索都指向那间尘封的档案室——以及档案室之下不为人知的地下室。

【内部指示】本阶段是终章。根据玩家积累的变量，引导剧情走向对应结局：
- ending_truth 需要 npc_trust>=5 且 keys 含"档案室钥匙"且 met_alan 为真。
- ending_escape 需要 keys 含"后门钥匙"且 diary_location 为"地下室"。
- ending_lost 在 npc_trust<=-3 时触发。
只有当对应条件确实满足时才在 ending 字段声明结局 id（由引擎校验），否则继续推进剧情。
