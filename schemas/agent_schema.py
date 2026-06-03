from pydantic import BaseModel


class AgentRequest(BaseModel):

    message: str


class AgentResponse(BaseModel):

    answer: str