from pydantic import BaseModel


class TaskCreate(BaseModel):

    description: str
    priority: str = "medium"


class TaskResponse(BaseModel):

    id: int
    description: str
    priority: str
    status: str

    model_config = {
        "from_attributes": True
    }