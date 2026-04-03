"use client";

import { useEffect, useState } from "react";
import { CheckCircle, XCircle, RefreshCw, FileText } from "lucide-react";

// Helper to get user auth header; server-side route handler injects agent credentials
const getAuthHeaders = (): Record<string, string> => {
    const headers: Record<string, string> = {};
    if (typeof window !== "undefined") {
        const token = localStorage.getItem("token");
        if (token) {
            headers["Authorization"] = `Bearer ${token}`;
        }
    }
    return headers;
};

interface CommitRecord {
    id: number;
    repo_name: string;
    commit_sha?: string | null;
    agent_id: string;
    bounty_id?: string | null;
    branch_name?: string | null;
    status: string;
    diff_summary: string;
    verification_exit_code?: number | null;
    verification_stdout?: string | null;
    trace_json?: unknown;
}

interface CommitDetail {
    record: CommitRecord;
    diff?: string | null;
}

export default function ReviewsPage() {
    const [pending, setPending] = useState<CommitRecord[]>([]);
    const [selected, setSelected] = useState<CommitDetail | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [isLoggedIn, setIsLoggedIn] = useState(false);

    useEffect(() => {
        if (typeof window === "undefined") {
            return;
        }
        setIsLoggedIn(Boolean(localStorage.getItem("token")));
    }, []);

    async function fetchPending() {
        const headers = getAuthHeaders();
        if (!headers.Authorization) {
            setPending([]);
            setSelected(null);
            setError(null);
            return;
        }

        try {
            setLoading(true);
            setError(null);
            const res = await fetch(`/api/v1/commits/pending`, {
                headers
            });
            if (res.status === 401) {
                setPending([]);
                setSelected(null);
                return;
            }
            if (!res.ok) {
                throw new Error(`Failed to fetch pending commits (${res.status})`);
            }
            const data = await res.json();
            setPending(data || []);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : "Failed to load");
        } finally {
            setLoading(false);
        }
    }

    async function fetchDetail(commitId: number) {
        try {
            const res = await fetch(`/api/v1/commits/${commitId}`, {
                headers: getAuthHeaders()
            });
            if (!res.ok) {
                throw new Error(`Failed to fetch commit ${commitId}`);
            }
            const data = await res.json();
            setSelected(data);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : "Failed to load detail");
        }
    }

    async function handleAction(commitId: number, action: "approve" | "reject") {
        try {
            setLoading(true);
            const res = await fetch(`/api/v1/commits/${commitId}/${action}`, {
                method: "POST",
                headers: getAuthHeaders()
            });
            if (!res.ok) {
                const body = await res.json().catch(() => ({}));
                throw new Error(body.detail || `Failed to ${action}`);
            }
            await fetchPending();
            if (selected?.record?.id === commitId) {
                setSelected(null);
            }
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : "Action failed");
        } finally {
            setLoading(false);
        }
    }

    useEffect(() => {
        if (!isLoggedIn) {
            setPending([]);
            setSelected(null);
            return;
        }
        fetchPending();
    }, [isLoggedIn]);

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-3xl font-bold text-white">Review Queue</h1>
                    <p className="text-zinc-400 text-sm">Pending commit reviews awaiting approve/reject decisions.</p>
                </div>
                <button
                    onClick={fetchPending}
                    disabled={!isLoggedIn}
                    className="flex items-center gap-2 px-3 py-1.5 text-xs rounded bg-zinc-800 text-zinc-300 hover:text-white disabled:opacity-50 disabled:cursor-not-allowed"
                >
                    <RefreshCw className="w-3.5 h-3.5" /> Refresh
                </button>
            </div>

            {!isLoggedIn ? (
                <div className="p-3 border border-zinc-800 bg-zinc-900/50 text-zinc-400 text-xs rounded">
                    Login required to view pending commit reviews.
                </div>
            ) : error && (
                <div className="p-3 border border-red-500/30 bg-red-500/10 text-red-300 text-xs rounded">
                    {error}
                </div>
            )}

            {loading ? (
                <div className="text-zinc-500 text-sm">Loading...</div>
            ) : pending.length === 0 ? (
                <div className="text-zinc-500 text-sm">No pending commits.</div>
            ) : (
                <div className="grid gap-3">
                    {pending.map((c) => (
                        <div key={c.id} className="glass-panel p-4 rounded-xl flex flex-col gap-2">
                            <div className="flex items-start justify-between gap-4">
                                <div className="space-y-1">
                                    <div className="text-sm text-zinc-300 font-mono">#{c.id} · {c.repo_name}</div>
                                    <div className="text-xs text-zinc-500">Agent: {c.agent_id}</div>
                                    <div className="text-sm text-zinc-200">{c.diff_summary}</div>
                                </div>
                                <div className="flex items-center gap-2">
                                    <button
                                        onClick={() => fetchDetail(c.id)}
                                        className="text-xs px-2 py-1 rounded bg-zinc-800 text-zinc-300 hover:text-white flex items-center gap-1"
                                    >
                                        <FileText className="w-3 h-3" /> Detail
                                    </button>
                                    <button
                                        onClick={() => handleAction(c.id, "approve")}
                                        disabled={loading}
                                        className="text-xs px-2 py-1 rounded bg-emerald-500/20 text-emerald-300 hover:bg-emerald-500/30 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1"
                                    >
                                        <CheckCircle className="w-3 h-3" /> Approve
                                    </button>
                                    <button
                                        onClick={() => handleAction(c.id, "reject")}
                                        disabled={loading}
                                        className="text-xs px-2 py-1 rounded bg-red-500/20 text-red-300 hover:bg-red-500/30 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1"
                                    >
                                        <XCircle className="w-3 h-3" /> Reject
                                    </button>
                                </div>
                            </div>
                            {c.verification_exit_code !== undefined && (
                                <div className="text-xs text-zinc-500">
                                    Verification: {c.verification_exit_code === 0 ? "passed" : c.verification_exit_code}
                                </div>
                            )}
                        </div>
                    ))}
                </div>
            )}

            {selected && (
                <div className="glass-panel p-4 rounded-xl">
                    <div className="text-sm text-zinc-300 font-mono mb-2">
                        Commit #{selected.record.id} · {selected.record.repo_name}
                    </div>
                    <div className="text-xs text-zinc-500 mb-3">
                        SHA: {selected.record.commit_sha || "n/a"}
                    </div>
                    {selected.diff ? (
                        <pre className="text-xs text-zinc-200 bg-black/40 p-3 rounded overflow-auto max-h-[400px] whitespace-pre-wrap">
                            {selected.diff}
                        </pre>
                    ) : (
                        <div className="text-xs text-zinc-500">No diff available.</div>
                    )}
                    {selected.record.trace_json != null && (
                        <pre className="text-[11px] text-zinc-400 mt-4 bg-black/30 p-3 rounded overflow-auto max-h-[300px] whitespace-pre-wrap">
                            {JSON.stringify(selected.record.trace_json, null, 2)}
                        </pre>
                    )}
                </div>
            )}
        </div>
    );
}
