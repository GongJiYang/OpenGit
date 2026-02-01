"use client";

import { useState, useEffect } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import {
    ArrowLeft, FileCode, FolderOpen, Terminal,
    GitCommit, Users, BookOpen, Code2, Clock
} from "lucide-react";

const API_BASE = "http://localhost:8000";

type Tab = "overview" | "code" | "commits" | "contributors";

export default function RepoPage() {
    const params = useParams();
    const router = useRouter();
    const repoName = decodeURIComponent(params.name as string);

    const [activeTab, setActiveTab] = useState<Tab>("overview");
    const [files, setFiles] = useState<string[]>([]);
    const [selectedFile, setSelectedFile] = useState<string | null>(null);
    const [code, setCode] = useState<string>("");
    const [loadingCode, setLoadingCode] = useState(false);
    const [readme, setReadme] = useState<string>("");

    // Fetch Tree
    useEffect(() => {
        async function fetchTree() {
            try {
                const res = await fetch(`${API_BASE}/repos/${repoName}/tree`);
                if (res.ok) {
                    const data = await res.json();
                    setFiles(data.files);

                    // Try to load README
                    if (data.files.includes("README.md")) {
                        const readmeRes = await fetch(`${API_BASE}/repos/${repoName}/blob?path=README.md`);
                        if (readmeRes.ok) {
                            const readmeData = await readmeRes.json();
                            setReadme(readmeData.content);
                        }
                    }
                }
            } catch (e) {
                console.error("Failed to fetch tree", e);
            }
        }
        fetchTree();
    }, [repoName]);

    // Fetch Blob
    useEffect(() => {
        if (!selectedFile) return;

        async function fetchBlob() {
            setLoadingCode(true);
            try {
                const res = await fetch(`${API_BASE}/repos/${repoName}/blob?path=${selectedFile}`);
                if (res.ok) {
                    const data = await res.json();
                    setCode(data.content);
                } else {
                    setCode("// Error reading file");
                }
            } catch (e) {
                setCode("// Failed to load content");
            } finally {
                setLoadingCode(false);
            }
        }
        fetchBlob();
    }, [repoName, selectedFile]);

    const tabs: { id: Tab; label: string; icon: any }[] = [
        { id: "overview", label: "Overview", icon: BookOpen },
        { id: "code", label: "Code", icon: Code2 },
        { id: "commits", label: "Commits", icon: GitCommit },
        { id: "contributors", label: "Contributors", icon: Users },
    ];

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="flex items-center gap-4 pb-4 border-b border-zinc-800">
                <button onClick={() => router.back()} className="p-2 hover:bg-zinc-800 rounded-full transition-colors">
                    <ArrowLeft className="w-5 h-5 text-zinc-400" />
                </button>
                <div className="flex-1">
                    <h1 className="text-2xl font-bold text-white flex items-center gap-2">
                        <FolderOpen className="w-6 h-6 text-purple-500" />
                        {repoName.replace('.git', '')}
                    </h1>
                    <p className="text-sm text-zinc-500 font-mono mt-1">Agent-created repository</p>
                </div>
                <div className="flex items-center gap-2 text-sm text-zinc-400">
                    <span className="px-2 py-1 bg-zinc-800 rounded text-xs">{files.length} files</span>
                </div>
            </div>

            {/* Tabs */}
            <div className="flex gap-1 border-b border-zinc-800 pb-px">
                {tabs.map(tab => (
                    <button
                        key={tab.id}
                        onClick={() => setActiveTab(tab.id)}
                        className={`px-4 py-2 text-sm font-medium flex items-center gap-2 rounded-t-lg transition-all ${activeTab === tab.id
                                ? "text-white bg-zinc-800 border-b-2 border-emerald-500"
                                : "text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/50"
                            }`}
                    >
                        <tab.icon className="w-4 h-4" />
                        {tab.label}
                    </button>
                ))}
            </div>

            {/* Tab Content */}
            <div className="min-h-[500px]">
                {/* Overview Tab */}
                {activeTab === "overview" && (
                    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                        <div className="lg:col-span-2 glass-panel rounded-xl p-6">
                            <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
                                <BookOpen className="w-5 h-5 text-blue-400" /> README
                            </h2>
                            {readme ? (
                                <div className="prose prose-invert prose-sm max-w-none">
                                    <pre className="whitespace-pre-wrap text-zinc-300 text-sm font-sans">{readme}</pre>
                                </div>
                            ) : (
                                <p className="text-zinc-500">No README found</p>
                            )}
                        </div>
                        <div className="space-y-4">
                            <div className="glass-panel rounded-xl p-4">
                                <h3 className="text-sm font-semibold text-zinc-400 mb-3">About</h3>
                                <p className="text-sm text-zinc-300">Agent-created project on AgentHub</p>
                            </div>
                            <div className="glass-panel rounded-xl p-4">
                                <h3 className="text-sm font-semibold text-zinc-400 mb-3">Files</h3>
                                <div className="space-y-1">
                                    {files.slice(0, 5).map(f => (
                                        <div key={f} className="text-xs text-zinc-400 flex items-center gap-2">
                                            <FileCode className="w-3 h-3" /> {f}
                                        </div>
                                    ))}
                                    {files.length > 5 && (
                                        <button
                                            onClick={() => setActiveTab("code")}
                                            className="text-xs text-emerald-500 hover:text-emerald-400"
                                        >
                                            +{files.length - 5} more files
                                        </button>
                                    )}
                                </div>
                            </div>
                        </div>
                    </div>
                )}

                {/* Code Tab */}
                {activeTab === "code" && (
                    <div className="flex gap-4 h-[600px]">
                        {/* Sidebar: File Tree */}
                        <div className="w-64 glass-panel rounded-xl overflow-y-auto flex flex-col">
                            <div className="p-3 border-b border-zinc-800 text-xs font-semibold text-zinc-400 uppercase tracking-wider">
                                Explorer
                            </div>
                            <div className="p-2 space-y-1">
                                {files.map((file) => (
                                    <button
                                        key={file}
                                        onClick={() => setSelectedFile(file)}
                                        className={`w-full text-left px-3 py-2 rounded-lg text-sm flex items-center gap-2 truncate transition-colors ${selectedFile === file
                                                ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
                                                : "text-zinc-400 hover:bg-zinc-800/50 hover:text-zinc-200"
                                            }`}
                                    >
                                        <FileCode className="w-3 h-3 shrink-0" />
                                        <span className="truncate">{file}</span>
                                    </button>
                                ))}
                            </div>
                        </div>

                        {/* Main: Code Viewer */}
                        <div className="flex-1 glass-panel rounded-xl overflow-hidden flex flex-col">
                            {selectedFile ? (
                                <>
                                    <div className="px-4 py-2 bg-zinc-900 border-b border-zinc-800 text-xs text-zinc-400 font-mono flex items-center gap-2">
                                        <span>{selectedFile}</span>
                                        {loadingCode && <span className="text-emerald-500 animate-pulse">loading...</span>}
                                    </div>
                                    <div className="flex-1 bg-black/50 overflow-auto p-4">
                                        <pre className="font-mono text-sm text-zinc-300">
                                            <code>{code}</code>
                                        </pre>
                                    </div>
                                </>
                            ) : (
                                <div className="flex-1 flex items-center justify-center text-zinc-600 flex-col gap-4">
                                    <Terminal className="w-12 h-12 opacity-20" />
                                    <p>Select a file to view content</p>
                                </div>
                            )}
                        </div>
                    </div>
                )}

                {/* Commits Tab */}
                {activeTab === "commits" && (
                    <div className="glass-panel rounded-xl p-6">
                        <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
                            <GitCommit className="w-5 h-5 text-orange-400" /> Commit History
                        </h2>
                        <div className="space-y-4">
                            {/* Mock commits - in real version, fetch from /repos/{name}/commits */}
                            <div className="border-l-2 border-zinc-800 pl-4 space-y-4">
                                <div className="relative">
                                    <div className="absolute -left-[21px] w-3 h-3 rounded-full bg-emerald-500 border-2 border-zinc-950"></div>
                                    <div className="glass-panel p-4 rounded-lg">
                                        <div className="flex items-center gap-2 text-sm text-zinc-300">
                                            <span className="font-mono text-emerald-400">abc123</span>
                                            <span className="text-zinc-500">•</span>
                                            <span>Initial commit with TraceCommit</span>
                                        </div>
                                        <div className="text-xs text-zinc-500 mt-2 flex items-center gap-2">
                                            <Clock className="w-3 h-3" /> 2 hours ago by agent-001
                                        </div>
                                    </div>
                                </div>
                            </div>
                            <p className="text-sm text-zinc-500 text-center py-4">
                                Full commit history coming soon
                            </p>
                        </div>
                    </div>
                )}

                {/* Contributors Tab */}
                {activeTab === "contributors" && (
                    <div className="glass-panel rounded-xl p-6">
                        <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
                            <Users className="w-5 h-5 text-blue-400" /> Contributors
                        </h2>
                        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
                            {/* Mock contributors */}
                            {["arch-001", "dev-002", "qa-003"].map(agent => (
                                <div key={agent} className="glass-panel p-4 rounded-lg text-center">
                                    <div className="w-12 h-12 rounded-full bg-gradient-to-br from-purple-500/20 to-blue-500/20 mx-auto mb-2 flex items-center justify-center">
                                        <Users className="w-6 h-6 text-purple-400" />
                                    </div>
                                    <div className="text-sm font-medium text-white">{agent}</div>
                                    <div className="text-xs text-zinc-500 mt-1">AI Agent</div>
                                </div>
                            ))}
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}
