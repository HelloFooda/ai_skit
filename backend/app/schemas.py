from pydantic import BaseModel, Field


class Character(BaseModel):
    name: str = Field(description="角色名")
    appearance: str = Field(description="外貌与服饰")
    stable_features: str = Field(description="稳定视觉特征，用于跨分镜保持一致")


class Prop(BaseModel):
    name: str = Field(description="道具名")
    appearance: str = Field(description="外观")
    state_changes: str = Field(default="", description="状态变化，无则空")


class Scene(BaseModel):
    name: str = Field(description="场景名")
    time: str = Field(description="时间")
    light: str = Field(description="光线")
    atmosphere: str = Field(description="氛围")


class Assets(BaseModel):
    characters: list[Character]
    props: list[Prop]
    scenes: list[Scene]


class ExtractAssetsRequest(BaseModel):
    chapter: str = Field(min_length=10)


class ExtractAssetsResponse(BaseModel):
    run_id: str
    model: str
    mock: bool
    assets: Assets
    latency_ms: int
