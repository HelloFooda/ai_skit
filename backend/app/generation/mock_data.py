"""离线样例数据：没有 API key 时用它跑通前后端联调。

从 `llm.py` 抽出来的原因：这 90 行是**测试夹具性质的内容**，和「消息组装 / 两段执行 /
校验」没有关系。放在业务模块里既撑大阅读面积，又容易在改逻辑时被顺手带上 diff。
现在它只被 `llm.extract_assets` 的 mock 分支（以及单测）引用，改样例不会碰到生产路径。

样例里刻意留了一处**机检越界**（第 3 镜 6s 与组时长 15s 对得上，但整体按 15s 上限走），
这样前端与 `check_storyboards` 的告警展示永远是有人验过的。
"""

from __future__ import annotations

from ..core.schemas import (
    Assets,
    Character,
    Prop,
    Scene,
    ScriptPackage,
    StoryboardGroup,
    build_blockings,
)

MOCK_ASSETS = Assets(
    characters=[
        Character(
            name="林桂芳",
            appearance="五十上下，短发齐耳，藏青工装外套洗得发白，裤脚沾着面灰",
            stable_features="左眉一道浅疤；右手虎口有旧烫伤",
            state="进店时外套肩头带雨气（场次 1 起），全章无换装",
            voice="中低音，语速偏慢，句尾习惯性下沉",
            prompt="五十上下的中年女性，身形偏瘦、肩背略佝，齐耳短发已掺灰白、发尾外翘；面容清瘦，左眉一道浅疤，右手虎口有旧烫伤；藏青工装外套洗得发白、袖口起毛，内衣领口露出灰布衬里，深色长裤裤脚沾着面灰，脚穿深色胶底布鞋；系一条蓝布围裙，布面有油渍与旧折痕",
        ),
        Character(
            name="沈砚舟",
            appearance="三十出头，黑色长风衣，金丝眼镜，头发梳得整齐",
            stable_features="右手无名指一枚旧银戒；眼镜左片有细微裂痕",
            state="全章无换装，外套扣子始终扣到第二颗",
            voice="中音，语速平稳，咬字清楚，带一点疲惫",
            prompt="三十出头的男性，身形清瘦挺拔、肩线平直；面容清隽，眉眼偏冷、鼻梁挺直，下颌线干净，黑发向后梳得整齐、鬓角利落；黑色及膝长风衣面料硬挺、肘部有轻微磨白，内穿深色高领毛衣，长裤裤线笔直，脚穿黑色皮鞋、鞋面擦得发亮；金丝眼镜左片有一道细微裂痕，右手无名指一枚旧银戒",
        ),
    ],
    props=[
        Prop(
            name="铜钥匙",
            owner="林桂芳",
            appearance="老旧黄铜，掌心大小，边缘磨得发亮",
            state_changes="从攥紧到插入锁孔",
            prompt="黄铜钥匙，长约四指、齿纹深且边缘磨圆，通体氧化发暗、握持处被磨得发亮，钥匙环上串着一枚旧铜牌，牌面字迹已磨损不可辨",
        ),
        Prop(
            name="怀表",
            owner="沈砚舟",
            appearance="银壳旧怀表，表盖有细密划痕",
            state_changes="开合一次",
            prompt="银壳怀表，直径约掌心的三分之二、壳面有细密划痕与浅坑，表盖铰链略松，打开后露出白色表盘与细黑指针，表冠有一圈防滑纹",
        ),
    ],
    scenes=[
        Scene(
            name="老饭馆内景",
            time="傍晚",
            light="暖黄顶灯为主光，木门处透进一道冷调夕光",
            atmosphere="烟火气、略嘈杂、桌椅陈旧",
            prompt="老街饭馆内景，木桌木椅成排摆放、桌面有油渍与刀痕，靠门一侧是玻璃木格窗，吧台在内侧靠墙、台上摆着搪瓷碗与暖瓶，地面铺旧水磨石，墙面下半截刷绿漆、上半截露砖，天花板正中悬一盏暖黄吊灯",
        )
    ],
)

MOCK_GROUPS = [
    StoryboardGroup(
        group_id="G01",
        duration=15.0,
        location="老饭馆内景",
        summary="林桂芳推门进店，与沈砚舟照面",
        shots=[
            {
                "shot_no": 1,
                "duration": 4.0,
                "shot_size": "全景",
                "angle": "平视",
                "camera": "机位固定",
                "function": "交代环境",
                "content": "木门被推开，林桂芳跨进门槛，暖黄顶灯把她整个人笼住",
                "dialogue": "",
                "blocking": "林桂芳｜画面左侧三分之一、近门处｜朝向画面内（右）｜机位在轴线左侧",
            },
            {
                "shot_no": 2,
                "duration": 5.0,
                "shot_size": "中景",
                "angle": "过肩",
                "camera": "缓慢右摇",
                "function": "推进",
                "content": "越过林桂芳肩头，沈砚舟在柜台后低头擦怀表，抬眼",
                "dialogue": "你怎么来了。",
                "blocking": "沈砚舟｜画面右侧、柜台后｜朝向画面外（左）｜机位仍在轴线左侧",
            },
            {
                "shot_no": 3,
                "duration": 6.0,
                "shot_size": "近景",
                "angle": "平视",
                "camera": "机位固定",
                "function": "反应",
                "content": "林桂芳摊开手掌，铜钥匙在灯下发亮，她攥紧",
                "dialogue": "钥匙我一直留着。",
                "blocking": "林桂芳｜画面居中偏左｜朝向画面内（右）｜机位在轴线左侧",
            },
        ],
    )
]


def mock_package(mode: str) -> ScriptPackage:
    """按 mode 拼一包样例产物：assets_only 只给三类资产，其余给全五类。"""
    assets = MOCK_ASSETS
    if mode == "assets_only":
        return ScriptPackage(
            characters=assets.characters, props=assets.props, scenes=assets.scenes
        )
    return ScriptPackage(
        characters=assets.characters,
        props=assets.props,
        scenes=assets.scenes,
        blockings=build_blockings(MOCK_GROUPS),
        storyboards=MOCK_GROUPS,
    )
