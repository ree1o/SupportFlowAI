from pydantic import BaseModel, ConfigDict


class AIStatusView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    state: str
    model: str
    digest: str | None
