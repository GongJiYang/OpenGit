import hashlib
import hmac
import json
import os
from typing import Any, Mapping, Optional


def _extract_agent_id(trace_data: Mapping[str, Any]) -> str:
    author = trace_data.get("author") if isinstance(trace_data, Mapping) else None
    if not isinstance(author, Mapping):
        raise ValueError("TraceCommit author is missing")
    agent_id = author.get("agent_id")
    if not isinstance(agent_id, str) or not agent_id.strip():
        raise ValueError("TraceCommit author.agent_id is missing")
    return agent_id


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha256_hex(text: str) -> str:
    if not isinstance(text, str):
        raise ValueError("Hash input must be a string")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def derive_agent_signing_key(secret: str, agent_id: str) -> bytes:
    if not secret:
        raise ValueError("Signing secret is required")
    if not agent_id:
        raise ValueError("agent_id is required")
    return hmac.new(secret.encode("utf-8"), agent_id.encode("utf-8"), hashlib.sha256).digest()


def compute_diff_hash_from_patch(patch_text: str) -> str:
    return _sha256_hex(patch_text or "")


def compute_reasoning_hash(reasoning_trace: Any) -> str:
    if not isinstance(reasoning_trace, list):
        raise ValueError("TraceCommit reasoning_trace must be a list")
    return _sha256_hex(_canonical_json(reasoning_trace))


def compute_binding_hash(trace_data: Mapping[str, Any]) -> str:
    if not isinstance(trace_data, Mapping):
        raise ValueError("TraceCommit payload must be an object")

    intent = trace_data.get("intent")
    if not isinstance(intent, Mapping):
        raise ValueError("TraceCommit intent is missing")

    reasoning_hash = trace_data.get("reasoning_hash")
    if not isinstance(reasoning_hash, str) or not reasoning_hash.strip():
        raise ValueError("TraceCommit reasoning_hash is missing")

    context_snapshot = trace_data.get("context_snapshot")
    if not isinstance(context_snapshot, Mapping):
        raise ValueError("TraceCommit context_snapshot is missing")

    file_paths = context_snapshot.get("file_paths")
    if not isinstance(file_paths, list):
        raise ValueError("TraceCommit context_snapshot.file_paths must be a list")

    binding_payload = {
        "protocol_version": trace_data.get("protocol_version"),
        "tree_hash": trace_data.get("tree_hash"),
        "diff_hash": trace_data.get("diff_hash"),
        "diff_summary": trace_data.get("diff_summary"),
        "reasoning_hash": reasoning_hash,
        "intent_description": intent.get("description"),
        "intent_category": intent.get("category"),
        "intent_vector": intent.get("vector"),
        "parent_sha": trace_data.get("parent_sha"),
        "author_agent_id": _extract_agent_id(trace_data),
        "context_file_paths": sorted(file_paths),
    }
    return _sha256_hex(_canonical_json(binding_payload))


def canonicalize_trace_commit(trace_data: Mapping[str, Any]) -> str:
    if not isinstance(trace_data, Mapping):
        raise ValueError("TraceCommit payload must be an object")
    payload = dict(trace_data)
    payload.pop("signature", None)
    return _canonical_json(payload)


def sign_trace_commit(
    trace_data: Mapping[str, Any],
    secret: str,
    *,
    agent_id: Optional[str] = None,
) -> str:
    resolved_agent_id = agent_id or _extract_agent_id(trace_data)
    signing_key = derive_agent_signing_key(secret, resolved_agent_id)
    canonical_payload = canonicalize_trace_commit(trace_data)
    return hmac.new(signing_key, canonical_payload.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_trace_commit_signature(trace_data: Mapping[str, Any], secret: str) -> bool:
    signature = trace_data.get("signature") if isinstance(trace_data, Mapping) else None
    if not isinstance(signature, str) or not signature:
        return False
    expected_signature = sign_trace_commit(trace_data, secret)
    return hmac.compare_digest(signature, expected_signature)


def get_trace_signing_secret(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    source = env if env is not None else os.environ
    secret = source.get("TRACE_COMMIT_SIGNING_SECRET")
    if secret and secret.strip():
        return secret.strip()
    return None


def is_trace_signature_required(env: Optional[Mapping[str, str]] = None) -> bool:
    source = env if env is not None else os.environ
    value = (source.get("TRACE_COMMIT_SIGNATURE_REQUIRED") or "1").strip().lower()
    return value not in {"0", "false", "no", "off"}
