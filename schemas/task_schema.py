from pydantic import BaseModel


class TaskCreate(BaseModel):

    description: str
    priority: str = "medium"


class TaskResponse(BaseModel):

    id: int
    description: str
    priority: str
    status: str
    source_id: int | None = None

    model_config = {
        "from_attributes": True
    }