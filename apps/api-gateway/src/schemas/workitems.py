from pydantic import BaseModel


class WorkItemListResponse(BaseModel):
    items: list
