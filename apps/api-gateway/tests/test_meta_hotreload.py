from meta.hotreload import HotReloadManager, ReloadAction, ReloadRule, ServiceConfig


def test_determine_actions_uses_explicit_priority_order():
    manager = HotReloadManager(
        services={"svc": ServiceConfig(name="svc", type="python", working_dir=".")},
        rules=[
            ReloadRule(path_pattern="apps/api-gateway/**/*.py", action=ReloadAction.RESTART, services=["svc"]),
            ReloadRule(path_pattern="apps/api-gateway/**/*.py", action=ReloadAction.RELOAD, services=["svc"]),
            ReloadRule(path_pattern="apps/api-gateway/**/*.py", action=ReloadAction.REBUILD, services=["svc"]),
        ],
    )

    actions = manager.determine_actions(["apps/api-gateway/src/main.py"])
    assert actions["svc"] == ReloadAction.REBUILD


def test_run_fallback_commands_runs_next_segment_on_failure(monkeypatch):
    manager = HotReloadManager()
    calls = []

    def fake_run(tokens, shell, capture_output, text, cwd=None):
        calls.append(tokens)

        class Result:
            def __init__(self, returncode):
                self.returncode = returncode
                self.stdout = ""
                self.stderr = ""

        if tokens[0] == "first":
            return Result(1)
        return Result(0)

    monkeypatch.setattr("meta.hotreload.subprocess.run", fake_run)

    result = manager._run_fallback_commands("first fail || second ok", cwd=".")
    assert result.returncode == 0
    assert calls == [["first", "fail"], ["second", "ok"]]


def test_run_safe_command_uses_shell_false(monkeypatch):
    manager = HotReloadManager()
    seen = {"shell": None}

    def fake_run(tokens, shell, capture_output, text, cwd=None):
        seen["shell"] = shell

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    monkeypatch.setattr("meta.hotreload.subprocess.run", fake_run)

    manager._run_safe_command("echo hello", cwd=".")
    assert seen["shell"] is False
