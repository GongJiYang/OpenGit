"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import {
    FileCode, ArrowLeft, GitCommit, X, Copy, Check,
    Bot, Code2, Activity
} from "lucide-react";
import { useParams } from "next/navigation";
import TaskBoard from "../../components/TaskBoard";

// Types
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

export default function RepoPage() {
    const params = useParams();
    const repoId = params.repoId as string;

    const [files, setFiles] = useState<string[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");

    // File viewer state
    const [selectedFile, setSelectedFile] = useState<string | null>(null);
    const [fileContent, setFileContent] = useState<string>("");
    const [fileLoading, setFileLoading] = useState(false);
    const [copied, setCopied] = useState(false);
    const [pendingVerifications, setPendingVerifications] = useState<PendingVerification[]>([]);

    const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "/api";

    useEffect(() => {
        async function fetchTree() {
            try {
                const res = await fetch(`${API_BASE}/repos/${repoId}/tree`);
                if (!res.ok) {
                    if (res.status === 404) throw new Error("Repository not found or empty");
                    throw new Error("Failed to fetch repository tree");
                }
                const data = await res.json();
                setFiles(data.files || []);
            } catch (err: any) {
                setError(err.message);
            } finally {
                setLoading(false);
            }
        }

        if (repoId) {
            fetchTree();
        }
    }, [repoId]);

    useEffect(() => {
        async function fetchPendingVerifications() {
            try {
                const apiBase = process.env.NEXT_PUBLIC_API_BASE || "/api";
                const agentKey = process.env.NEXT_PUBLIC_AGENT_API_KEY || "";
                if (!agentKey) return;
                const res = await fetch(`${apiBase}/api/v1/commits/pending/verification?repo_name=${repoId}`, {
                    headers: { "X-API-Key": agentKey }
                });
                if (!res.ok) return;
                const data = await res.json();
                setPendingVerifications(data || []);
            } catch {
                // ignore
            }
        }
        if (repoId) {
            fetchPendingVerifications();
        }
    }, [repoId]);

    async function handleFileClick(filename: string) {
        setSelectedFile(filename);
        setFileLoading(true);
        setFileContent("");

        try {
            const res = await fetch(`${API_BASE}/repos/${repoId}/blob?path=${encodeURIComponent(filename)}`);
            if (!res.ok) throw new Error("Failed to load file");
            const data = await res.json();
            setFileContent(data.content || "// Empty file");
        } catch (err) {
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
                                <h1 className="text-3xl font-bold text-emerald-400 font-mono">{repoId}</h1>
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

                    <div className="glass-panel rounded-2xl">
                        <div className="p-4 border-b border-white/5 flex items-center gap-2">
                            <Activity className="w-4 h-4 text-zinc-400" />
                            <h2 className="text-sm font-medium text-zinc-400">Recent Activity</h2>
                        </div>
                        <div className="p-4 text-xs text-zinc-500">
                            No activity data available yet.
                        </div>
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
                    <TaskBoard repoId={repoId} />

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
