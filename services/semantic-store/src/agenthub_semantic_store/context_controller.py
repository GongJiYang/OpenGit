from typing import List, Optional
from .indexer import VectorIndexer

class ContextController:
    """
    Manages the Context Window for Agents.
    'Smart Pruning' logic resides here.
    """
    
    def __init__(self, indexer: VectorIndexer):
        self.indexer = indexer

    def prune_context(self, query: str, repo_id: str, max_tokens: int = 4000) -> str:
        """
        Retrieves the most relevant code chunks for the query, 
        fitting within max_tokens.
        """
        # 1. Search Vector Store
        # Retrieve more than we need so we can re-rank or filter
        hits = self.indexer.search(query, limit=20, repo_id=repo_id)
        
        selected_code = []
        current_tokens = 0
        
        # Simple heuristic: 1 token ~= 4 chars (very rough)
        # Better: use tiktoken, but for MVP char count is okay.
        max_chars = max_tokens * 4
        
        print(f"✂️ Pruning context for query: '{query}' (Max chars: {max_chars})")

        for hit in hits:
            payload = hit['payload']
            snippet = f"\n# File: {payload['file_path']} | {payload['chunk_name']}\n{payload['code_snippet']}\n"
            
            snippet_len = len(snippet)
            if current_tokens + snippet_len < max_chars:
                selected_code.append(snippet)
                current_tokens += snippet_len
            else:
                # Context full
                break
                
        final_context = "\n".join(selected_code)
        print(f"✅ Constructed Context: {len(final_context)} chars from {len(selected_code)} chunks.")
        
        if not final_context:
            return "# No relevant context found."
            
        return final_context
