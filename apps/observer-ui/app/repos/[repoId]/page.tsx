"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import {
    FileCode, ArrowLeft, GitCommit, X, Copy, Check,
    Bot, Code2, Activity, Loader2, Play, CheckCircle, XCircle, Shield, Server
} from "lucide-react";
import { useParams } from "next/navigation";
import TaskBoard from "../../components/TaskBoard";

// Types
interface CIJob {
    id: string;
    bounty_id: string;
    repo_id: string | null;
    runner_id: string | null;
    status: "pending" | "assigned" | "running" | "completed" | "failed" | "timeout" | "audit_failed";
    execution_mode: "shared_local" | "self_hosted" | "yolo_mode";
    test_command: string;
    exit_code: number | null;
    passed: boolean | null;
    is_audited: boolean;
    audit_result: string | null;
    created_at: string;
    started_at: string | null;
    completed_at: string | null;
}

interface PendingVerification {
    commit_id: number;
    repo_name: string;
    bounty_id: string;
    verification_mode: string;
    verification_exit_code?: number | null;
    verification_stdout?: string | null;
    diff_summary: string;
    agent_id: string;
}

interface RepoMeta {
    id: string;
    full_name: string;
    name: string;
    owner: string;
}

export default function RepoPage() {
    const params = useParams();
    const repoId = params.repoId as string;

    const [files, setFiles] = useState<string[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [repoName, setRepoName] = useState<string>("");
    const [repoDisplayName, setRepoDisplayName] = useState<string>(repoId);

    // File viewer state
    const [selectedFile, setSelectedFile] = useState<string | null>(null);
    const [fileContent, setFileContent] = useState<string>("");
    const [fileLoading, setFileLoading] = useState(false);
    const [copied, setCopied] = useState(false);
    const [pendingVerifications, setPendingVerifications] = useState<PendingVerification[]>([]);

    const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "/api";

    useEffect(() => {
        async function resolveRepoAndFetchTree() {
            try {
                setLoading(true);
                setError("");

                const repoMetaRes = await fetch(`${API_BASE}/v1/repos/${repoId}`);
                if (!repoMetaRes.ok) {
                    if (repoMetaRes.status === 404) throw new Error("Repository not found");
                    throw new Error("Failed to fetch repository metadata");
                }

                const repoMeta = await repoMetaRes.json() as RepoMeta;
                const resolvedRepoName = repoMeta.full_name;
                setRepoName(resolvedRepoName);
                setRepoDisplayName(resolvedRepoName);

                const treeRes = await fetch(`${API_BASE}/repos/${encodeURIComponent(resolvedRepoName)}/tree`);
                if (!treeRes.ok) {
                    if (treeRes.status === 404) throw new Error("Repository not found or empty");
                    throw new Error("Failed to fetch repository tree");
                }
                const data = await treeRes.json();
                setFiles(data.files || []);
            } catch (err: unknown) {
                setError(err instanceof Error ? err.message : "Failed to fetch repository tree");
            } finally {
                setLoading(false);
            }
        }

        if (repoId) {
            resolveRepoAndFetchTree();
        }
    }, [API_BASE, repoId]);

    useEffect(() => {
        async function fetchPendingVerifications() {
            try {
                const apiBase = process.env.NEXT_PUBLIC_API_BASE || "/api";
                if (!repoName) return;
                const res = await fetch(`${apiBase}/v1/commits/pending/verification?repo_name=${encodeURIComponent(repoName)}`);
                if (!res.ok) return;
                const data = await res.json();
                setPendingVerifications(data || []);
            } catch {
                // ignore
            }
        }
        if (repoName) {
            fetchPendingVerifications();
        }
    }, [repoName]);

    async function handleFileClick(filename: string) {
        setSelectedFile(filename);
        setFileLoading(true);
        setFileContent("");

        try {
            if (!repoName) {
                throw new Error("Repository not resolved");
            }
            const res = await fetch(`${API_BASE}/repos/${encodeURIComponent(repoName)}/blob?path=${encodeURIComponent(filename)}`);
            if (!res.ok) throw new Error("Failed to load file");
            const data = await res.json();
            setFileContent(data.content || "// Empty file");
        } catch {
            setFileContent("// Error loading file content");
        } finally {
            setFileLoading(false);
        }
    }

    function handleCopy() {
        navigator.clipboard.writeText(fileContent);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    }

    return (
        <div className="space-y-8">
            {/* Back Link */}
            <Link href="/explore" className="inline-flex items-center gap-2 text-zinc-400 hover:text-white transition-colors text-sm">
                <ArrowLeft className="w-4 h-4" />
                Back to Explore
            </Link>

            {/* Hero Header */}
            <div className="glass-panel rounded-2xl p-6 relative overflow-hidden">
                <div className="absolute -right-20 -top-20 w-64 h-64 bg-emerald-500/10 blur-[80px] rounded-full" />

                <div className="relative z-10">
                    <div className="flex items-start justify-between mb-4">
                        <div>
                            <div className="flex items-center gap-3 mb-2">
                                <h1 className="text-3xl font-bold text-emerald-400 font-mono">{repoDisplayName}</h1>
                            </div>
                            <p className="text-zinc-400 text-sm max-w-2xl">No repository metadata available yet.</p>
                        </div>
                    </div>
                </div>
            </div>

            {/* Main Grid */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">

                {/* Left Column: Files + Code */}
                <div className="lg:col-span-2 space-y-6">
                    {/* File Browser + Code Viewer */}
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        {/* File List */}
                        <div className="glass-panel rounded-2xl min-h-[350px]">
                            <div className="p-4 border-b border-white/5 flex items-center gap-2">
                                <Code2 className="w-4 h-4 text-zinc-400" />
                                <h2 className="text-sm font-medium text-zinc-400">Files</h2>
                                <span className="ml-auto text-xs text-zinc-600">{files.length} files</span>
                            </div>
                            {loading ? (
                                <div className="flex items-center justify-center h-64 text-zinc-500 animate-pulse">
                                    Loading...
                                </div>
                            ) : error ? (
                                <div className="flex flex-col items-center justify-center h-64 text-red-400 gap-2 p-4">
                                    <span className="text-sm">❌ {error}</span>
                                </div>
                            ) : files.length === 0 ? (
                                <div className="flex flex-col items-center justify-center h-64 text-zinc-500 gap-2">
                                    <GitCommit className="w-8 h-8 opacity-20" />
                                    <p className="text-sm">Empty repository</p>
                                </div>
                            ) : (
                                <div className="divide-y divide-white/5 max-h-[300px] overflow-auto">
                                    {files.map((file, i) => (
                                        <button
                                            key={i}
                                            onClick={() => handleFileClick(file)}
                                            className={`w-full flex items-center gap-3 p-3 hover:bg-white/5 transition-colors text-left ${selectedFile === file ? "bg-emerald-500/10 border-l-2 border-emerald-400" : ""
                                                }`}
                                        >
                                            <FileCode className={`w-4 h-4 ${selectedFile === file ? "text-emerald-400" : "text-blue-400"}`} />
                                            <span className={`font-mono text-xs ${selectedFile === file ? "text-emerald-300" : "text-zinc-300"}`}>
                                                {file}
                                            </span>
                                        </button>
                                    ))}
                                </div>
                            )}
                        </div>

                        {/* Code Viewer */}
                        <div className="glass-panel rounded-2xl min-h-[350px] flex flex-col">
                            <div className="p-4 border-b border-white/5 flex items-center justify-between">
                                <h2 className="text-sm font-medium text-zinc-400 truncate">
                                    {selectedFile ? `📄 ${selectedFile}` : "Select a file"}
                                </h2>
                                {selectedFile && (
                                    <div className="flex gap-1">
                                        <button onClick={handleCopy} className="p-1.5 rounded hover:bg-white/10 text-zinc-400 hover:text-white" title="Copy">
                                            {copied ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                                        </button>
                                        <button onClick={() => { setSelectedFile(null); setFileContent(""); }} className="p-1.5 rounded hover:bg-white/10 text-zinc-400 hover:text-white" title="Close">
                                            <X className="w-3.5 h-3.5" />
                                        </button>
                                    </div>
                                )}
                            </div>
                            <div className="flex-1 overflow-auto p-4">
                                {!selectedFile ? (
                                    <div className="flex items-center justify-center h-full text-zinc-600 text-sm">
                                        👈 Click a file to preview
                                    </div>
                                ) : fileLoading ? (
                                    <div className="flex items-center justify-center h-full text-zinc-500 animate-pulse text-sm">
                                        Loading...
                                    </div>
                                ) : (
                                    <pre className="text-xs font-mono text-zinc-300 whitespace-pre-wrap break-words leading-relaxed">
                                        <code>{fileContent}</code>
                                    </pre>
                                )}
                            </div>
                        </div>
                    </div>

                    {/* CI Job History */}
                    <div className="glass-panel rounded-2xl">
                        <div className="p-4 border-b border-white/5 flex items-center gap-2">
                            <Activity className="w-4 h-4 text-zinc-400" />
                            <h2 className="text-sm font-medium text-zinc-400">CI Job History</h2>
                        </div>
                        <CIJobHistory repoId={repoId} />
                    </div>
                </div>

                {/* Right Column: Sidebar */}
                <div className="space-y-6">
                    {pendingVerifications.length > 0 && (
                        <div className="glass-panel rounded-2xl">
                            <div className="p-4 border-b border-white/5 flex items-center gap-2">
                                <Activity className="w-4 h-4 text-zinc-400" />
                                <h2 className="text-sm font-medium text-zinc-400">Pending Verification</h2>
                            </div>
                            <div className="divide-y divide-white/5">
                                {pendingVerifications.map(v => (
                                    <div key={v.commit_id} className="p-4 text-xs text-zinc-400">
                                        <div className="flex items-center justify-between mb-1">
                                            <span className="text-zinc-300 font-mono">#{v.commit_id}</span>
                                            <span className="text-zinc-500">{v.verification_mode}</span>
                                        </div>
                                        <div className="text-zinc-300 mb-2">{v.diff_summary}</div>
                                        {v.verification_stdout && (
                                            <pre className="text-[10px] text-zinc-500 whitespace-pre-wrap break-words">
                                                {v.verification_stdout}
                                            </pre>
                                        )}
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}

                    {/* Tasks / Bounty Board */}
                    <TaskBoard repoId={repoName || repoId} />

                    <div className="glass-panel rounded-2xl">
                        <div className="p-4 border-b border-white/5 flex items-center gap-2">
                            <Bot className="w-4 h-4 text-zinc-400" />
                            <h2 className="text-sm font-medium text-zinc-400">Contributors</h2>
                        </div>
                        <div className="p-4 text-xs text-zinc-500">
                            No contributor data available yet.
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}

// CI Job History Component
function CIJobHistory({ repoId }: { repoId: string }) {
    const [jobs, setJobs] = useState<CIJob[]>([]);
    const [loading, setLoading] = useState(true);
    const [filter, setFilter] = useState<string>("all");

    const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "/api";

    useEffect(() => {
        fetchJobs();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [repoId, filter]);

    const fetchJobs = async () => {
        try {
            setLoading(true);
            const params = new URLSearchParams();
            if (filter !== "all") {
                params.append("status_filter", filter);
            }
            params.append("limit", "10");

            const res = await fetch(`${API_BASE}/v1/repos/${repoId}/jobs?${params.toString()}`);
            if (res.ok) {
                setJobs(await res.json());
            }
        } catch (e) {
            console.error("Failed to fetch jobs:", e);
        } finally {
            setLoading(false);
        }
    };

    const getStatusColor = (status: string) => {
        const colors: Record<string, string> = {
            completed: "bg-green-500/10 text-green-400",
            failed: "bg-red-500/10 text-red-400",
            running: "bg-yellow-500/10 text-yellow-400",
            pending: "bg-zinc-500/10 text-zinc-400",
            assigned: "bg-blue-500/10 text-blue-400",
            timeout: "bg-orange-500/10 text-orange-400",
            audit_failed: "bg-red-500/10 text-red-400",
        };
        return colors[status] || "bg-zinc-500/10 text-zinc-400";
    };

    return (
        <div className="p-4">
            {/* Filter Tabs */}
            <div className="flex gap-2 mb-4">
                {["all", "completed", "failed", "running"].map((s) => (
                    <button
                        key={s}
                        onClick={() => setFilter(s)}
                        className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${
                            filter === s
                                ? "bg-emerald-500/20 text-emerald-400 border border-emerald-500/30"
                                : "bg-zinc-800 text-zinc-400 hover:text-white"
                        }`}
                    >
                        {s.charAt(0).toUpperCase() + s.slice(1)}
                    </button>
                ))}
            </div>

            {loading ? (
                <div className="flex items-center justify-center py-8">
                    <Loader2 className="w-5 h-5 text-zinc-500 animate-spin" />
                </div>
            ) : jobs.length === 0 ? (
                <div className="text-center py-8 text-zinc-500">
                    <Play className="w-6 h-6 mx-auto mb-2 opacity-50" />
                    <p className="text-xs">No CI jobs found for this repository.</p>
                </div>
            ) : (
                <div className="space-y-2 max-h-[300px] overflow-auto">
                    {jobs.map((job) => (
                        <div
                            key={job.id}
                            className="bg-zinc-800/50 rounded-lg p-3"
                        >
                            <div className="flex items-start justify-between gap-3">
                                <div className="flex-1 min-w-0">
                                    <div className="flex items-center gap-2 mb-1">
                                        <code className="text-[10px] text-zinc-500 font-mono">
                                            #{job.id.slice(0, 8)}
                                        </code>
                                        <span className={`text-[10px] px-1.5 py-0.5 rounded ${getStatusColor(job.status)}`}>
                                            {job.status}
                                        </span>
                                        {job.is_audited && (
                                            <span className="flex items-center gap-1 text-[10px] text-purple-400">
                                                <Shield className="w-2.5 h-2.5" />
                                                Audited
                                            </span>
                                        )}
                                    </div>
                                    <p className="text-[11px] text-zinc-300 font-mono truncate">
                                        {job.test_command}
                                    </p>
                                    <div className="flex items-center gap-3 mt-1.5 text-[10px] text-zinc-500">
                                        <span className="flex items-center gap-1">
                                            <Server className="w-2.5 h-2.5" />
                                            {job.execution_mode.replace("_", " ")}
                                        </span>
                                        {job.exit_code !== null && (
                                            <span className={job.exit_code === 0 ? "text-green-400" : "text-red-400"}>
                                                Exit: {job.exit_code}
                                            </span>
                                        )}
                                    </div>
                                </div>
                                <div className="text-right text-[10px] text-zinc-500">
                                    <p>{new Date(job.created_at).toLocaleDateString()}</p>
                                    {job.completed_at && job.started_at && (
                                        <p className="text-zinc-600">
                                            {Math.round((new Date(job.completed_at).getTime() - new Date(job.started_at).getTime()) / 1000)}s
                                        </p>
                                    )}
                                </div>
                            </div>
                            {job.passed !== null && (
                                <div className="mt-2 pt-2 border-t border-zinc-700/50 flex items-center gap-2">
                                    {job.passed ? (
                                        <>
                                            <CheckCircle className="w-3 h-3 text-green-400" />
                                            <span className="text-[10px] text-green-400">Passed</span>
                                        </>
                                    ) : (
                                        <>
                                            <XCircle className="w-3 h-3 text-red-400" />
                                            <span className="text-[10px] text-red-400">Failed</span>
                                        </>
                                    )}
                                    {job.audit_result && (
                                        <span className={`text-[10px] ml-auto ${
                                            job.audit_result === "passed" ? "text-green-400" : "text-red-400"
                                        }`}>
                                            Audit: {job.audit_result}
                                        </span>
                                    )}
                                </div>
                            )}
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}
