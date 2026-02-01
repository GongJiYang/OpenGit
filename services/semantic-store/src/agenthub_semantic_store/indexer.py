import os
import uuid
import time
from typing import List, Dict, Any, Optional
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from .ast_parser import CodeChunk
from .embeddings import ZhipuEmbedding

class VectorIndexer:
    """
    Real Vector Database using Qdrant (or compatible API).
    """

    def __init__(self, collection_name: str = "agenthub_codebase", embedding_dim: int = 1024):
        # Note: Zhipu 'embedding-2' is 1024 dim usually, whereas OpenAI is 1536. 
        # We default to 1024 now.
        self.collection_name = collection_name
        self.embedding_dim = embedding_dim
        
        # Connect to Qdrant
        # If QDRANT_URL is not set, tries local memory.
        url = os.getenv("QDRANT_URL", ":memory:")
        api_key = os.getenv("QDRANT_API_KEY")
        
        if url == ":memory:":
             self.client = QdrantClient(location=":memory:")
        else:
             self.client = QdrantClient(url=url, api_key=api_key)

        self.embedder = ZhipuEmbedding()
        
        # Ensure collection exists
        self._ensure_collection()

    def _ensure_collection(self):
        collections = self.client.get_collections().collections
        exists = any(c.name == self.collection_name for c in collections)
        
        if not exists:
            print(f"📦 Creating collection '{self.collection_name}'...")
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(size=self.embedding_dim, distance=Distance.COSINE)
            )

    def index_chunk(self, repo_id: str, file_path: str, chunk: CodeChunk):
        """
        Index a single code chunk.
        """
        try:
            # Generate ID
            # Deterministic ID based on content location so we can support upserts nicely?
            # Or just random. Random is safer for collision but harder to update.
            # Let's use deterministic hash for MVP to allow "re-indexing" same file updates.
            point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{repo_id}:{file_path}:{chunk.name}"))
            
            # Embed
            vector = self.embedder.get_embedding(chunk.code)
            
            payload = {
                "repo_id": repo_id,
                "file_path": file_path,
                "chunk_name": chunk.name,
                "chunk_type": chunk.type,
                "code_snippet": chunk.code,
                "docstring": chunk.docstring,
                "timestamp": time.time()
            }
            
            self.client.upsert(
                collection_name=self.collection_name,
                points=[
                    PointStruct(
                        id=point_id,
                        vector=vector,
                        payload=payload
                    )
                ]
            )
            print(f"💽 Indexed: {chunk.name} ({chunk.type})")
            
        except Exception as e:
            print(f"❌ Failed to index {chunk.name}: {e}")

    def search(self, query: str, limit: int = 5, repo_id: Optional[str] = None) -> List[Dict]:
        """
        Semantic search.
        """
        try:
            vector = self.embedder.get_embedding(query)
            
            # Filter by repo_id if provided
            query_filter = None
            if repo_id:
                from qdrant_client.models import Filter, FieldCondition, MatchValue
                query_filter = Filter(
                    must=[
                        FieldCondition(
                            key="repo_id",
                            match=MatchValue(value=repo_id)
                        )
                    ]
                )

            # Use query_points which is lower level but robust across versions for Local/Remote
            results = self.client.query_points(
                collection_name=self.collection_name,
                query=vector,
                query_filter=query_filter,
                limit=limit
            ).points
            
            start_results = []
            for hit in results:
                start_results.append({
                    "score": hit.score,
                    "payload": hit.payload
                })
            return start_results
            
        except Exception as e:
            print(f"❌ Search failed: {e}")
            return []

if __name__ == "__main__":
    # Test if run directly (requires env vars)
    try:
        idx = VectorIndexer()
        print(f"Indexer initialized. Collection: {idx.collection_name}")
    except Exception as e:
        print(f"Indexer init failed: {e}")
