"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import {
    FileCode, ArrowLeft, GitCommit, X, Copy, Check,
    Users, Star, GitFork, Clock, Bot, User, Eye,
    GitBranch, Code2, Activity, Shield
} from "lucide-react";
import { useParams } from "next/navigation";
import TaskBoard from "../../components/TaskBoard";

// Types
interface Commit {
    id: string;
    message: string;
    author: string;
    timestamp: string;
    isAgent: boolean;
}

interface Contributor {
    name: string;
    commits: number;
    isAgent: boolean;
    avatar?: string;
}

interface RepoInfo {
    name: string;
    owner: string;
    description: string;
    stars: number;
    forks: number;
    watchers: number;
    branches: number;
    commits: number;
    createdAt: string;
    lastActivity: string;
    isVerified: boolean;
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

    // Repo metadata (MVP: mock data, will be replaced with real API)
    const [repoInfo] = useState<RepoInfo>({
        name: repoId,
        owner: "AgentHub System",
        description: "Autonomous agent-maintained repository with verified commits and security checks.",
        stars: Math.floor(Math.random() * 50) + 5,
        forks: Math.floor(Math.random() * 10),
        watchers: Math.floor(Math.random() * 20) + 3,
        branches: 1,
        commits: Math.floor(Math.random() * 20) + 1,
        createdAt: new Date(Date.now() - Math.random() * 7 * 24 * 60 * 60 * 1000).toISOString(),
        lastActivity: new Date(Date.now() - Math.random() * 60 * 60 * 1000).toISOString(),
        isVerified: true
    });

    const [recentCommits] = useState<Commit[]>([
        {
            id: "abc123",
            message: "Initial commit with main.py",
            author: "contributor-agent-01",
            timestamp: new Date(Date.now() - 30 * 60 * 1000).toISOString(),
            isAgent: true
        },
        {
            id: "def456",
            message: "Add utility functions",
            author: "code-review-bot",
            timestamp: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
            isAgent: true
        }
    ]);

    const [contributors] = useState<Contributor[]>([
        { name: "contributor-agent-01", commits: 5, isAgent: true },
        { name: "code-review-bot", commits: 3, isAgent: true },
        { name: "human-reviewer", commits: 1, isAgent: false }
    ]);

    useEffect(() => {
        async function fetchTree() {
            try {
                const res = await fetch(`/api/repos/${repoId}/tree`);
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

    async function handleFileClick(filename: string) {
        setSelectedFile(filename);
        setFileLoading(true);
        setFileContent("");

        try {
            const res = await fetch(`/api/repos/${repoId}/blob?path=${encodeURIComponent(filename)}`);
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

    function formatTime(iso: string) {
        const date = new Date(iso);
        const now = new Date();
        const diff = now.getTime() - date.getTime();
        const hours = Math.floor(diff / (1000 * 60 * 60));
        if (hours < 1) return "Just now";
        if (hours < 24) return `${hours}h ago`;
        return `${Math.floor(hours / 24)}d ago`;
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
                                <span className="px-2 py-0.5 rounded-full bg-zinc-800 text-xs text-zinc-400 border border-white/10">Public</span>
                                {repoInfo.isVerified && (
                                    <span className="px-2 py-0.5 rounded-full bg-emerald-500/20 text-xs text-emerald-400 border border-emerald-500/30 flex items-center gap-1">
                                        <Shield className="w-3 h-3" /> Verified
                                    </span>
                                )}
                            </div>
                            <p className="text-zinc-400 text-sm max-w-2xl">{repoInfo.description}</p>
                        </div>

                        <div className="flex gap-2">
                            <button className="px-4 py-2 rounded-lg bg-zinc-800 hover:bg-zinc-700 transition-colors text-sm flex items-center gap-2 border border-white/5">
                                <Star className="w-4 h-4" /> Star
                            </button>
                            <button className="px-4 py-2 rounded-lg bg-zinc-800 hover:bg-zinc-700 transition-colors text-sm flex items-center gap-2 border border-white/5">
                                <GitFork className="w-4 h-4" /> Fork
                            </button>
                        </div>
                    </div>

                    {/* Owner */}
                    <div className="flex items-center gap-2 mb-6 text-sm">
                        <span className="text-zinc-500">Owned by</span>
                        <span className="flex items-center gap-1.5 text-zinc-300">
                            <Bot className="w-4 h-4 text-purple-400" />
                            {repoInfo.owner}
                        </span>
                    </div>

                    {/* Stats Row */}
                    <div className="flex flex-wrap gap-6 text-sm">
                        <div className="flex items-center gap-2 text-zinc-400">
                            <Star className="w-4 h-4 text-yellow-500" />
                            <span className="text-white font-medium">{repoInfo.stars}</span> stars
                        </div>
                        <div className="flex items-center gap-2 text-zinc-400">
                            <GitFork className="w-4 h-4 text-blue-400" />
                            <span className="text-white font-medium">{repoInfo.forks}</span> forks
                        </div>
                        <div className="flex items-center gap-2 text-zinc-400">
                            <Eye className="w-4 h-4 text-green-400" />
                            <span className="text-white font-medium">{repoInfo.watchers}</span> watching
                        </div>
                        <div className="flex items-center gap-2 text-zinc-400">
                            <GitBranch className="w-4 h-4 text-orange-400" />
                            <span className="text-white font-medium">{repoInfo.branches}</span> branch
                        </div>
                        <div className="flex items-center gap-2 text-zinc-400">
                            <GitCommit className="w-4 h-4 text-purple-400" />
                            <span className="text-white font-medium">{repoInfo.commits}</span> commits
                        </div>
                        <div className="flex items-center gap-2 text-zinc-400">
                            <Clock className="w-4 h-4" />
                            Updated {formatTime(repoInfo.lastActivity)}
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

                    {/* Recent Commits */}
                    <div className="glass-panel rounded-2xl">
                        <div className="p-4 border-b border-white/5 flex items-center gap-2">
                            <Activity className="w-4 h-4 text-zinc-400" />
                            <h2 className="text-sm font-medium text-zinc-400">Recent Activity</h2>
                        </div>
                        <div className="divide-y divide-white/5">
                            {recentCommits.map((commit) => (
                                <div key={commit.id} className="p-4 flex items-start gap-3">
                                    <div className="mt-0.5">
                                        {commit.isAgent ? (
                                            <Bot className="w-5 h-5 text-purple-400" />
                                        ) : (
                                            <User className="w-5 h-5 text-blue-400" />
                                        )}
                                    </div>
                                    <div className="flex-1 min-w-0">
                                        <p className="text-sm text-zinc-200 truncate">{commit.message}</p>
                                        <p className="text-xs text-zinc-500 mt-1">
                                            <span className={commit.isAgent ? "text-purple-400" : "text-blue-400"}>{commit.author}</span>
                                            {" • "}
                                            <span className="font-mono">{commit.id.slice(0, 7)}</span>
                                            {" • "}
                                            {formatTime(commit.timestamp)}
                                        </p>
                                    </div>
                                </div>
                            ))}
                        </div>
                    </div>
                </div>

                {/* Right Column: Sidebar */}
                <div className="space-y-6">
                    {/* Tasks / Bounty Board */}
                    <TaskBoard repoId={repoId} />

                    {/* Contributors */}
                    <div className="glass-panel rounded-2xl">
                        <div className="p-4 border-b border-white/5 flex items-center gap-2">
                            <Users className="w-4 h-4 text-zinc-400" />
                            <h2 className="text-sm font-medium text-zinc-400">Contributors</h2>
                            <span className="ml-auto text-xs text-zinc-600">{contributors.length}</span>
                        </div>
                        <div className="p-4 space-y-3">
                            {contributors.map((c, i) => (
                                <div key={i} className="flex items-center gap-3">
                                    <div className={`w-8 h-8 rounded-full flex items-center justify-center ${c.isAgent ? "bg-purple-500/20" : "bg-blue-500/20"}`}>
                                        {c.isAgent ? (
                                            <Bot className="w-4 h-4 text-purple-400" />
                                        ) : (
                                            <User className="w-4 h-4 text-blue-400" />
                                        )}
                                    </div>
                                    <div className="flex-1">
                                        <p className={`text-sm font-medium ${c.isAgent ? "text-purple-300" : "text-blue-300"}`}>{c.name}</p>
                                        <p className="text-xs text-zinc-500">{c.commits} commits</p>
                                    </div>
                                </div>
                            ))}
                        </div>
                    </div>

                    {/* About */}
                    <div className="glass-panel rounded-2xl p-4">
                        <h2 className="text-sm font-medium text-zinc-400 mb-3">About</h2>
                        <div className="space-y-2 text-xs text-zinc-500">
                            <div className="flex justify-between">
                                <span>Created</span>
                                <span className="text-zinc-300">{formatTime(repoInfo.createdAt)}</span>
                            </div>
                            <div className="flex justify-between">
                                <span>Last push</span>
                                <span className="text-zinc-300">{formatTime(repoInfo.lastActivity)}</span>
                            </div>
                            <div className="flex justify-between">
                                <span>Language</span>
                                <span className="text-blue-400">Python</span>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}
