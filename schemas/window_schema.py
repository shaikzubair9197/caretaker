from pydantic import BaseModel


class ActiveWindowRequest(BaseModel):
    window_title: str