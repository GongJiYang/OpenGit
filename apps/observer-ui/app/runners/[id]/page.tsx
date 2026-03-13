"use client";

import { useState, useEffect } from "react";
import { useRouter, useParams } from "next/navigation";
import {
    Server, ArrowLeft, Settings, Trash2, Plus, X, Check,
    Activity, Clock, Cpu, Terminal, AlertCircle, Loader2,
    Globe, Lock, LogIn
} from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "/api";

interface Runner {
    id: string;
    name: string;
    status: "online" | "offline" | "busy" | "disabled" | "banned";
    cpu_cores: number | null;
    memory_gb: number | null;
    os_type: string | null;
    os_version: string | null;
    docker_version: string | null;
    total_jobs_completed: number;
    total_compute_seconds: number;
    total_earnings: number;
    reputation_score: number;
    last_heartbeat_at: string | null;
    created_at: string;
    allowed_repo_ids: string[];
    is_global: boolean;
}

interface Repo {
    id: string;
    full_name: string;
    name: string;
    owner: string;
    description: string | null;
}

export default function RunnerDetailPage() {
    const router = useRouter();
    const params = useParams();
    const runnerId = params.id as string;

    const [runner, setRunner] = useState<Runner | null>(null);
    const [loading, setLoading] = useState(true);
    const [isLoggedIn, setIsLoggedIn] = useState(false);

    // Repo binding state
    const [availableRepos, setAvailableRepos] = useState<Repo[]>([]);
    const [showRepoSelector, setShowRepoSelector] = useState(false);
    const [isGlobalMode, setIsGlobalMode] = useState(true);
    const [saving, setSaving] = useState(false);

    useEffect(() => {
        const token = localStorage.getItem("token");
        setIsLoggedIn(!!token);
        if (token) {
            fetchRunner();
            fetchRepos();
        } else {
            setLoading(false);
        }
    }, [runnerId]);

    const getAuthHeaders = () => {
        const token = localStorage.getItem("token");
        const userId = localStorage.getItem("user_id");
        const headers: Record<string, string> = {
            "Content-Type": "application/json",
        };
        if (token) {
            headers["Authorization"] = `Bearer ${token}`;
        }
        if (userId) {
            headers["X-User-Id"] = userId;
        }
        return headers;
    };

    const fetchRunner = async () => {
        try {
            setLoading(true);
            const res = await fetch(`${API_BASE}/v1/runners/${runnerId}`, {
                headers: getAuthHeaders()
            });
            if (res.ok) {
                const data = await res.json();
                setRunner(data);
                setIsGlobalMode(data.is_global);
            } else if (res.status === 401) {
                localStorage.removeItem("token");
                setIsLoggedIn(false);
            }
        } catch (e) {
            console.error(e);
        } finally {
            setLoading(false);
        }
    };

    const fetchRepos = async () => {
        try {
            const res = await fetch(`${API_BASE}/v1/repos?mine=true`, {
                headers: getAuthHeaders()
            });
            if (res.ok) {
                setAvailableRepos(await res.json());
            }
        } catch (e) {
            console.error(e);
        }
    };

    const updateRepoBindings = async (allowedRepoIds: string[], isGlobal: boolean) => {
        if (!runner) return;

        try {
            setSaving(true);
            const res = await fetch(`${API_BASE}/v1/runners/${runnerId}/repos`, {
                method: "PUT",
                headers: getAuthHeaders(),
                body: JSON.stringify({
                    allowed_repo_ids: allowedRepoIds,
                    is_global: isGlobal
                })
            });

            if (res.ok) {
                const data = await res.json();
                setRunner(prev => prev ? {
                    ...prev,
                    allowed_repo_ids: data.allowed_repo_ids,
                    is_global: data.is_global
                } : null);
                setIsGlobalMode(data.is_global);
            }
        } catch (e) {
            console.error(e);
        } finally {
            setSaving(false);
        }
    };

    const addRepo = async (repoId: string) => {
        try {
            setSaving(true);
            const res = await fetch(`${API_BASE}/v1/runners/${runnerId}/repos/${repoId}`, {
                method: "POST",
                headers: getAuthHeaders()
            });

            if (res.ok) {
                const data = await res.json();
                setRunner(prev => prev ? {
                    ...prev,
                    allowed_repo_ids: data.allowed_repo_ids,
                    is_global: data.is_global
                } : null);
                setIsGlobalMode(data.is_global);
            }
        } catch (e) {
            console.error(e);
        } finally {
            setSaving(false);
        }
    };

    const removeRepo = async (repoId: string) => {
        try {
            setSaving(true);
            const res = await fetch(`${API_BASE}/v1/runners/${runnerId}/repos/${repoId}`, {
                method: "DELETE",
                headers: getAuthHeaders()
            });

            if (res.ok) {
                const data = await res.json();
                setRunner(prev => prev ? {
                    ...prev,
                    allowed_repo_ids: data.allowed_repo_ids,
                    is_global: data.is_global
                } : null);
            }
        } catch (e) {
            console.error(e);
        } finally {
            setSaving(false);
        }
    };

    const toggleGlobalMode = () => {
        const newMode = !isGlobalMode;
        setIsGlobalMode(newMode);
        updateRepoBindings(runner?.allowed_repo_ids || [], newMode);
    };

    const formatUptime = (seconds: number) => {
        const hours = Math.floor(seconds / 3600);
        const mins = Math.floor((seconds % 3600) / 60);
        if (hours > 0) return `${hours}h ${mins}m`;
        return `${mins}m`;
    };

    const getStatusColor = (status: string) => {
        const colors: Record<string, string> = {
            online: "text-green-400 bg-green-500/10 border-green-500/20",
            busy: "text-yellow-400 bg-yellow-500/10 border-yellow-500/20",
            offline: "text-zinc-400 bg-zinc-500/10 border-zinc-500/20",
            disabled: "text-zinc-500 bg-zinc-500/10 border-zinc-500/20",
            banned: "text-red-400 bg-red-500/10 border-red-500/20",
        };
        return colors[status] || "text-zinc-400 bg-zinc-500/10";
    };

    const getReputationColor = (score: number) => {
        if (score >= 80) return "text-emerald-400";
        if (score >= 50) return "text-yellow-400";
        return "text-red-400";
    };

    const getBoundRepoName = (repoId: string) => {
        const repo = availableRepos.find(r => r.id === repoId);
        return repo ? repo.full_name : repoId.slice(0, 8) + "...";
    };

    const unboundRepos = availableRepos.filter(
        r => !runner?.allowed_repo_ids.includes(r.id)
    );

    if (loading) {
        return (
            <div className="text-center py-16">
                <Loader2 className="w-8 h-8 text-zinc-500 animate-spin mx-auto mb-4" />
                <p className="text-zinc-500">Loading...</p>
            </div>
        );
    }

    if (!isLoggedIn) {
        return (
            <div className="glass-panel rounded-xl p-12 text-center">
                <Server className="w-16 h-16 text-zinc-600 mx-auto mb-4" />
                <h3 className="text-xl font-semibold text-white mb-2">Login Required</h3>
                <p className="text-zinc-400 mb-6">
                    Login to view and manage your runner details
                </p>
                <button
                    onClick={() => router.push("/login")}
                    className="px-6 py-3 bg-purple-500 hover:bg-purple-600 text-white font-medium rounded-lg inline-flex items-center gap-2"
                >
                    <LogIn className="w-5 h-5" />
                    Go to Login
                </button>
            </div>
        );
    }

    if (!runner) {
        return (
            <div className="glass-panel rounded-xl p-12 text-center">
                <AlertCircle className="w-16 h-16 text-red-500 mx-auto mb-4" />
                <h3 className="text-xl font-semibold text-white mb-2">Runner Not Found</h3>
                <p className="text-zinc-400 mb-6">
                    The runner you're looking for doesn't exist or you don't have access.
                </p>
                <button
                    onClick={() => router.push("/runners")}
                    className="px-6 py-3 bg-zinc-700 hover:bg-zinc-600 text-white font-medium rounded-lg inline-flex items-center gap-2"
                >
                    <ArrowLeft className="w-5 h-5" />
                    Back to Runners
                </button>
            </div>
        );
    }

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="flex items-center gap-4">
                <button
                    onClick={() => router.push("/runners")}
                    className="p-2 text-zinc-400 hover:text-white hover:bg-zinc-800 rounded-lg transition-colors"
                >
                    <ArrowLeft className="w-5 h-5" />
                </button>
                <div className="flex-1">
                    <div className="flex items-center gap-3">
                        <h1 className="text-2xl font-bold text-white">{runner.name}</h1>
                        <span className={`text-xs px-2 py-0.5 rounded-full border ${getStatusColor(runner.status)}`}>
                            {runner.status}
                        </span>
                    </div>
                    <p className="text-zinc-400 text-sm mt-1">
                        Runner ID: {runner.id}
                    </p>
                </div>
            </div>

            {/* Stats Grid */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div className="glass-panel rounded-xl p-4">
                    <p className="text-xs text-zinc-500">Reputation</p>
                    <p className={`text-2xl font-bold ${getReputationColor(runner.reputation_score)}`}>
                        {runner.reputation_score}
                    </p>
                </div>
                <div className="glass-panel rounded-xl p-4">
                    <p className="text-xs text-zinc-500">Jobs Completed</p>
                    <p className="text-2xl font-bold text-white">{runner.total_jobs_completed}</p>
                </div>
                <div className="glass-panel rounded-xl p-4">
                    <p className="text-xs text-zinc-500">Total Runtime</p>
                    <p className="text-2xl font-bold text-white">{formatUptime(runner.total_compute_seconds)}</p>
                </div>
                <div className="glass-panel rounded-xl p-4">
                    <p className="text-xs text-zinc-500">Total Earnings</p>
                    <p className="text-2xl font-bold text-emerald-400">
                        ${(runner.total_earnings / 100).toFixed(2)}
                    </p>
                </div>
            </div>

            {/* System Info */}
            <div className="glass-panel rounded-xl p-5">
                <h3 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
                    <Cpu className="w-5 h-5 text-purple-400" />
                    System Information
                </h3>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                    <div>
                        <p className="text-zinc-500">CPU Cores</p>
                        <p className="text-white">{runner.cpu_cores || "Unknown"}</p>
                    </div>
                    <div>
                        <p className="text-zinc-500">Memory</p>
                        <p className="text-white">{runner.memory_gb ? `${runner.memory_gb} GB` : "Unknown"}</p>
                    </div>
                    <div>
                        <p className="text-zinc-500">OS</p>
                        <p className="text-white">
                            {runner.os_type ? `${runner.os_type} ${runner.os_version || ""}` : "Unknown"}
                        </p>
                    </div>
                    <div>
                        <p className="text-zinc-500">Docker</p>
                        <p className="text-white">{runner.docker_version || "Unknown"}</p>
                    </div>
                </div>
            </div>

            {/* Repository Binding */}
            <div className="glass-panel rounded-xl p-5">
                <div className="flex items-center justify-between mb-4">
                    <h3 className="text-lg font-semibold text-white flex items-center gap-2">
                        {isGlobalMode ? (
                            <Globe className="w-5 h-5 text-emerald-400" />
                        ) : (
                            <Lock className="w-5 h-5 text-purple-400" />
                        )}
                        Repository Binding
                    </h3>
                    <button
                        onClick={toggleGlobalMode}
                        disabled={saving}
                        className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors flex items-center gap-2 ${
                            isGlobalMode
                                ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 hover:bg-emerald-500/20"
                                : "bg-purple-500/10 text-purple-400 border border-purple-500/20 hover:bg-purple-500/20"
                        }`}
                    >
                        {isGlobalMode ? (
                            <>
                                <Globe className="w-4 h-4" />
                                Global Mode
                            </>
                        ) : (
                            <>
                                <Lock className="w-4 h-4" />
                                Specific Repos
                            </>
                        )}
                    </button>
                </div>

                <p className="text-zinc-400 text-sm mb-4">
                    {isGlobalMode
                        ? "This runner serves all repositories. It will accept jobs from any repo."
                        : "This runner only serves specific repositories. Add repos below to allow jobs from them."}
                </p>

                {/* Bound Repos List */}
                {!isGlobalMode && (
                    <>
                        <div className="space-y-2 mb-4">
                            {runner.allowed_repo_ids.length === 0 ? (
                                <div className="text-center py-8 text-zinc-500">
                                    <Lock className="w-8 h-8 mx-auto mb-2 opacity-50" />
                                    <p>No repositories bound. Add repos to allow this runner to serve them.</p>
                                </div>
                            ) : (
                                runner.allowed_repo_ids.map((repoId) => (
                                    <div
                                        key={repoId}
                                        className="flex items-center justify-between bg-zinc-800/50 rounded-lg px-4 py-3"
                                    >
                                        <div className="flex items-center gap-3">
                                            <div className="w-8 h-8 rounded-lg bg-purple-500/20 flex items-center justify-center">
                                                <Lock className="w-4 h-4 text-purple-400" />
                                            </div>
                                            <span className="text-white">{getBoundRepoName(repoId)}</span>
                                        </div>
                                        <button
                                            onClick={() => removeRepo(repoId)}
                                            disabled={saving}
                                            className="p-1.5 text-zinc-500 hover:text-red-400 hover:bg-red-500/10 rounded transition-colors"
                                        >
                                            <X className="w-4 h-4" />
                                        </button>
                                    </div>
                                ))
                            )}
                        </div>

                        {/* Add Repo Button */}
                        <button
                            onClick={() => setShowRepoSelector(true)}
                            disabled={saving || unboundRepos.length === 0}
                            className="w-full py-3 border border-dashed border-zinc-700 rounded-lg text-zinc-400 hover:border-purple-500/50 hover:text-purple-400 transition-colors flex items-center justify-center gap-2 disabled:opacity-50"
                        >
                            <Plus className="w-4 h-4" />
                            Add Repository
                        </button>
                    </>
                )}

                {/* Repo Selector Modal */}
                {showRepoSelector && (
                    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
                        <div className="glass-panel rounded-2xl p-6 max-w-md w-full mx-4">
                            <h2 className="text-xl font-bold text-white mb-4">Add Repository</h2>
                            <div className="space-y-2 max-h-64 overflow-y-auto">
                                {unboundRepos.map((repo) => (
                                    <button
                                        key={repo.id}
                                        onClick={() => {
                                            addRepo(repo.id);
                                            setShowRepoSelector(false);
                                        }}
                                        className="w-full text-left bg-zinc-800 hover:bg-zinc-700 rounded-lg px-4 py-3 transition-colors"
                                    >
                                        <p className="text-white font-medium">{repo.full_name}</p>
                                        {repo.description && (
                                            <p className="text-zinc-500 text-sm truncate">{repo.description}</p>
                                        )}
                                    </button>
                                ))}
                            </div>
                            <button
                                onClick={() => setShowRepoSelector(false)}
                                className="mt-4 w-full py-2 bg-zinc-700 hover:bg-zinc-600 text-white rounded-lg"
                            >
                                Cancel
                            </button>
                        </div>
                    </div>
                )}
            </div>

            {/* Danger Zone */}
            <div className="glass-panel rounded-xl p-5 border border-red-500/20">
                <h3 className="text-lg font-semibold text-red-400 mb-4">Danger Zone</h3>
                <p className="text-zinc-400 text-sm mb-4">
                    Disabling this runner will stop it from accepting new jobs. This action can be reversed.
                </p>
                <button
                    className="px-4 py-2 bg-red-500/10 hover:bg-red-500/20 text-red-400 border border-red-500/20 rounded-lg flex items-center gap-2"
                >
                    <Trash2 className="w-4 h-4" />
                    Disable Runner
                </button>
            </div>
        </div>
    );
}
