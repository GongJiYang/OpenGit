from pydantic import BaseModel


class SearchResponse(BaseModel):
    chunk_name: str
    code_snippet: str
    score: float
