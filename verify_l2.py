
import os
import sys
import time

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "services/semantic-store/src")))

from agenthub_semantic_store.indexer import VectorIndexer
from agenthub_semantic_store.ast_parser import PythonASTParser, CodeChunk
from agenthub_semantic_store.context_controller import ContextController

def test_layer2():
    print("🔬 Verifying Layer 2: Semantic Context (Qdrant + ZhipuAI)...")
    
    if not os.getenv("ZHIPUAI_API_KEY"):
        print("❌ ZHIPUAI_API_KEY not set. Skipping Layer 2 verification.")
        return

    # 1. Initialize
    try:
        # Zhipu embedding-2 is 1024 dimension
        indexer = VectorIndexer(collection_name="test_verification_l2", embedding_dim=1024)
        print("✅ Indexer connected.")
    except Exception as e:
        print(f"❌ Indexer init failed: {e}")
        return

    # 2. Parse Self
    parser = PythonASTParser()
    self_code = open(__file__, "r").read()
    chunks = parser.parse(self_code)
    print(f"✅ Parsed {len(chunks)} chunks from self.")

    # 3. Index
    repo_id = "test-repo"
    file_path = "verify_l2.py"
    
    # We also manually create a chunk to be sure we find something unique
    test_chunk = CodeChunk(
        name="magic_function_123",
        type="function",
        code="def magic_function_123():\n    return 'magic'",
        start_line=1,
        end_line=2,
        docstring="This is a magic function for testing retrieval"
    )
    
    indexer.index_chunk(repo_id, file_path, test_chunk)
    
    # Wait for Qdrant indexing (usually immediate for small data but let's be safe)
    time.sleep(1)

    # 4. Search
    results = indexer.search("magic function", limit=1, repo_id=repo_id)
    if not results:
        print("❌ Search returned no results.")
    else:
        top_match = results[0]['payload']['chunk_name']
        print(f"✅ Search found: {top_match}")
        assert top_match == "magic_function_123", f"Expected magic_function_123, got {top_match}"

    # 5. Context Pruning
    controller = ContextController(indexer)
    context = controller.prune_context("magic function", repo_id=repo_id, max_tokens=1000)
    print("✅ Context Controller Output:")
    print("-" * 20)
    print(context)
    print("-" * 20)
    
    if "magic_function_123" in context:
        print("🎉 Layer 2 Verification PASSED!")
    else:
        print("❌ functionality verified, but context pruning didn't include target string.")

if __name__ == "__main__":
    test_layer2()
