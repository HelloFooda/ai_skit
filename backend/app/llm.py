import json
import time

from openai import OpenAI

from .config import settings
from .schemas import Assets, Character, Prop, Scene

_PROMPT = """你是影视资产盘点助手。从下面小说章节中抽取用于视频制作的资产，只分三类：人物、道具、场景。
规则：
1. 只基于原文，不虚构；原文信息不足的字段写"未知"。
2. 严格返回 JSON，不要 markdown 代码块、不要任何解释。
结构：
{{
  "characters": [{{"name": "", "appearance": "", "stable_features": ""}}],
  "props": [{{"name": "", "appearance": "", "state_changes": ""}}],
  "scenes": [{{"name": "", "time": "", "light": "", "atmosphere": ""}}]
}}

章节原文：
{chapter}
"""

# mock 数据：没填 API key 时用它跑通前后端联调
_MOCK_ASSETS = Assets(
    characters=[
        Character(name="林桂芳", appearance="短发，藏青工装", stable_features="左眉一道浅疤"),
        Character(name="沈砚舟", appearance="长风衣，金丝眼镜", stable_features="右手无名指一枚旧戒"),
    ],
    props=[
        Prop(name="铜钥匙", appearance="老旧黄铜，掌心大小", state_changes=""),
        Prop(name="怀表", appearance="银壳旧怀表", state_changes="开合"),
    ],
    scenes=[
        Scene(name="老饭馆内景", time="傍晚", light="暖黄顶灯", atmosphere="烟火气、略嘈杂"),
    ],
)


def _client() -> OpenAI:
    return OpenAI(base_url=settings.base_url, api_key=settings.api_key)


def extract_assets(chapter: str) -> tuple[Assets, bool, int]:
    """最底层的一步：一次 LLM 调用 + Pydantic 强校验。

    返回 (assets, is_mock, latency_ms)。
    没填 api_key 时自动走 mock，保证骨架随时能跑。
    """
    use_mock = settings.mock or not settings.api_key
    if use_mock:
        time.sleep(0.2)
        return _MOCK_ASSETS, True, 200

    t0 = time.time()
    client = _client()
    resp = client.chat.completions.create(
        model=settings.model,
        messages=[{"role": "user", "content": _PROMPT.format(chapter=chapter)}],
        temperature=0.2,
    )
    text = resp.choices[0].message.content or ""
    # 教学点：LLM 返回的是纯文本，要自己解析成结构化对象并校验。
    # 这一步是 agent 最底层——后面所有"工具调用""状态机"都建立在它之上。
    data = json.loads(text)
    assets = Assets.model_validate(data)
    return assets, False, int((time.time() - t0) * 1000)
