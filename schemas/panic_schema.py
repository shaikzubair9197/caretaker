from pydantic import BaseModel


class PanicDumpRequest(BaseModel):
    text: str


class PanicDumpResponse(BaseModel):
    intent: str
    action: str
    details: str