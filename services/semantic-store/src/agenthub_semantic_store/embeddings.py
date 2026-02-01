from typing import List
import os
from zhipuai import ZhipuAI

class ZhipuEmbedding:
    def __init__(self, model: str = "embedding-2"):
        # API Key should be set in env ZHIPUAI_API_KEY or passed here
        api_key = os.getenv("ZHIPUAI_API_KEY")
        self.client = ZhipuAI(api_key=api_key)
        self.model = model

    def get_embedding(self, text: str) -> List[float]:
        try:
            response = self.client.embeddings.create(
                model=self.model,
                input=text
            )
            # ZhipuAI returns object similar to OpenAI with data[0].embedding
            return response.data[0].embedding
        except Exception as e:
            print(f"Error getting embedding from Zhipu: {e}")
            raise e
