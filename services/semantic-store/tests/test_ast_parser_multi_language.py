from agenthub_semantic_store.ast_parser import SemanticParser



def test_semantic_parser_extracts_python_symbols():
    parser = SemanticParser()

    chunks = parser.parse(
        """
class Worker:
    def run(self):
        return True

async def sync_all():
    return 1
""".strip(),
        file_path="worker.py",
    )

    chunk_map = {(chunk.type, chunk.name) for chunk in chunks}
    assert ("class", "Worker") in chunk_map
    assert ("function", "run") in chunk_map
    assert ("async_function", "sync_all") in chunk_map



def test_semantic_parser_extracts_typescript_symbols():
    parser = SemanticParser()

    chunks = parser.parse(
        """
export interface TaskItem {
  id: string
}

export async function loadTasks() {
  return []
}

const renderTask = () => "ok"
""".strip(),
        file_path="task.ts",
    )

    chunk_map = {(chunk.type, chunk.name) for chunk in chunks}
    assert ("interface", "TaskItem") in chunk_map
    assert ("async_function", "loadTasks") in chunk_map
    assert ("function", "renderTask") in chunk_map



def test_semantic_parser_extracts_go_symbols():
    parser = SemanticParser()

    chunks = parser.parse(
        """
type Runner struct {
    name string
}

type Store interface {
    Save() error
}

func Build() error {
    return nil
}

func (r Runner) Execute() error {
    return nil
}
""".strip(),
        file_path="runner.go",
    )

    chunk_map = {(chunk.type, chunk.name) for chunk in chunks}
    assert ("class", "Runner") in chunk_map
    assert ("interface", "Store") in chunk_map
    assert ("function", "Build") in chunk_map
    assert ("method", "Execute") in chunk_map



def test_semantic_parser_extracts_config_sections():
    parser = SemanticParser()

    chunks = parser.parse(
        """
app:
  name: demo
  features:
    search: true
queue:
  enabled: true
""".strip(),
        file_path="settings.yaml",
    )

    chunk_map = {(chunk.type, chunk.name) for chunk in chunks}
    assert ("config_section", "app") in chunk_map
    assert ("config_section", "features") in chunk_map
    assert ("config_section", "queue") in chunk_map



def test_semantic_parser_falls_back_to_file_windows_for_unknown_extensions():
    parser = SemanticParser()
    source = "\n".join(f"line {idx}" for idx in range(1, 66))

    chunks = parser.parse(source, file_path="notes.txt")

    assert len(chunks) == 2
    assert chunks[0].type == "file_window"
    assert chunks[0].name == "notes.txt:1-60"
    assert chunks[1].name == "notes.txt:61-65"



def test_semantic_parser_falls_back_when_python_source_is_invalid():
    parser = SemanticParser()

    chunks = parser.parse("def broken(:\n    pass\n", file_path="broken.py")

    assert len(chunks) == 1
    assert chunks[0].type == "file_window"
    assert chunks[0].name == "broken.py:1-2"
