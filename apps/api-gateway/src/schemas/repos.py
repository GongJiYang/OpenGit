from pydantic import BaseModel


class CreateRepoRequest(BaseModel):
    name: str
