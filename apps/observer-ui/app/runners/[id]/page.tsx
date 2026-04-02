"use client";

import { useState, useEffect } from "react";
import { useRouter, useParams } from "next/navigation";
import {
    Server, ArrowLeft, Settings, Trash2, Plus, X, Check,
    Activity, Clock, Cpu, Terminal, AlertCircle, Loader2,
    Globe, Lock, LogIn, Play, CheckCircle, XCircle, Shield,
    RefreshCw, AlertTriangle, UserCheck, ArrowDownCircle
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

interface Job {
    id: string;
    bounty_id: string;
    repo_id: string | null;
    runner_id: string | null;
    status: "pending" | "assigned" | "running" | "completed" | "failed" | "timeout" | "audit_failed" | "partial_pass" | "human_review";
    execution_mode: "shared_local" | "self_hosted" | "yolo_mode";
    test_command: string;
    exit_code: number | null;
    passed: boolean | null;
    is_audited: boolean;
    audit_result: string | null;
    created_at: string;
    started_at: string | null;
    completed_at: string | null;
    // Recovery-related fields
    retry_count: number;
    max_retries: number;
    failure_reason: string | null;
    failure_severity: "critical" | "warning" | "info" | null;
    used_fallback: boolean;
    original_runner_id: string | null;
    requires_manual_review: boolean;
    // Test results
    total_tests: number;
    passed_tests: number;
    failed_tests: number;
    warnings: Array<{ type: string; message?: string }> | null;
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

    // Job history state
    const [jobs, setJobs] = useState<Job[]>([]);
    const [jobsLoading, setJobsLoading] = useState(false);
    const [jobFilter, setJobFilter] = useState<string>("all");

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

    // Fetch jobs when runner is loaded
    useEffect(() => {
        if (runner && isLoggedIn) {
            fetchJobs();
        }
    }, [runner, jobFilter]);

    const fetchJobs = async () => {
        if (!runner) return;
        try {
            setJobsLoading(true);
            const params = new URLSearchParams();
            if (jobFilter !== "all") {
                params.append("status", jobFilter);
            }
            params.append("limit", "20");

            const res = await fetch(`${API_BASE}/v1/runners/${runnerId}/jobs?${params.toString()}`, {
                headers: getAuthHeaders()
            });
            if (res.ok) {
                setJobs(await res.json());
            }
        } catch (e) {
            console.error("Failed to fetch jobs:", e);
        } finally {
            setJobsLoading(false);
        }
    };

    const getAuthHeaders = () => {
        const token = localStorage.getItem("token");
        const headers: Record<string, string> = {
            "Content-Type": "application/json",
        };
        if (token) {
            headers["Authorization"] = `Bearer ${token}`;
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
            console.log("Fetching repos with URL:", `${API_BASE}/v1/repos?mine=true`);
            const res = await fetch(`${API_BASE}/v1/repos?mine=true`, {
                headers: getAuthHeaders()
            });
            console.log("Repos response status:", res.status);
            if (res.ok) {
                const data = await res.json();
                console.log("Fetched repos:", data);
                setAvailableRepos(data);
            } else {
                const errorData = await res.json().catch(() => ({}));
                console.error("Failed to fetch repos:", res.status, errorData);
            }
        } catch (e) {
            console.error("Error fetching repos:", e);
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
            console.log("Adding repo:", repoId, "to runner:", runnerId);
            const res = await fetch(`${API_BASE}/v1/runners/${runnerId}/repos/${repoId}`, {
                method: "POST",
                headers: getAuthHeaders()
            });

            console.log("Response status:", res.status);
            if (res.ok) {
                const data = await res.json();
                console.log("Response data:", data);
                setRunner(prev => prev ? {
                    ...prev,
                    allowed_repo_ids: data.allowed_repo_ids,
                    is_global: data.is_global
                } : null);
                setIsGlobalMode(data.is_global);
            } else {
                const errorData = await res.json().catch(() => ({}));
                console.error("Failed to add repo:", res.status, errorData);
                alert(`Failed to add repository: ${errorData.detail || res.statusText}`);
            }
        } catch (e) {
            console.error("Error adding repo:", e);
            alert(`Error adding repository: ${e}`);
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

    // Debug logging for repo binding
    useEffect(() => {
        if (runner && availableRepos.length > 0) {
            console.log("Runner allowed_repo_ids:", runner.allowed_repo_ids);
            console.log("Available repos:", availableRepos.map(r => r.id));
            console.log("Unbound repos:", unboundRepos.map(r => r.id));
        }
    }, [runner, availableRepos, unboundRepos]);

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
                    The runner you&apos;re looking for doesn&apos;t exist or you don&apos;t have access.
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
                            title={availableRepos.length === 0 ? "No repositories available. Create a repository first." : unboundRepos.length === 0 ? "All repositories are already bound to this runner." : ""}
                            className="w-full py-3 border border-dashed border-zinc-700 rounded-lg text-zinc-400 hover:border-purple-500/50 hover:text-purple-400 transition-colors flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                            <Plus className="w-4 h-4" />
                            {availableRepos.length === 0 ? "No Repositories Available" : "Add Repository"}
                        </button>
                        {availableRepos.length === 0 && (
                            <p className="text-xs text-zinc-500 text-center mt-2">
                                Create a repository first to bind it to this runner.
                            </p>
                        )}
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

            {/* Job History */}
            <div className="glass-panel rounded-xl p-5">
                <div className="flex items-center justify-between mb-4">
                    <h3 className="text-lg font-semibold text-white flex items-center gap-2">
                        <Activity className="w-5 h-5 text-blue-400" />
                        Job History
                    </h3>
                    <div className="flex gap-2 flex-wrap">
                        {["all", "completed", "failed", "running", "partial_pass", "human_review"].map((s) => (
                            <button
                                key={s}
                                onClick={() => setJobFilter(s)}
                                className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${
                                    jobFilter === s
                                        ? "bg-blue-500/20 text-blue-400 border border-blue-500/30"
                                        : "bg-zinc-800 text-zinc-400 hover:text-white"
                                }`}
                            >
                                {s === "partial_pass" ? "Partial Pass" :
                                 s === "human_review" ? "Human Review" :
                                 s.charAt(0).toUpperCase() + s.slice(1)}
                            </button>
                        ))}
                    </div>
                </div>

                {jobsLoading ? (
                    <div className="text-center py-8">
                        <Loader2 className="w-6 h-6 text-zinc-500 animate-spin mx-auto mb-2" />
                        <p className="text-zinc-500 text-sm">Loading jobs...</p>
                    </div>
                ) : jobs.length === 0 ? (
                    <div className="text-center py-8 text-zinc-500">
                        <Play className="w-8 h-8 mx-auto mb-2 opacity-50" />
                        <p>No jobs found for this runner.</p>
                    </div>
                ) : (
                    <div className="space-y-3">
                        {jobs.map((job) => (
                            <div
                                key={job.id}
                                className="bg-zinc-800/50 rounded-lg p-4"
                            >
                                <div className="flex items-start justify-between gap-4">
                                    <div className="flex-1 min-w-0">
                                        <div className="flex items-center gap-2 mb-1 flex-wrap">
                                            <code className="text-xs text-zinc-400 font-mono">
                                                #{job.id.slice(0, 8)}
                                            </code>
                                            <span className={`text-xs px-1.5 py-0.5 rounded ${
                                                job.status === "completed" ? "bg-green-500/10 text-green-400" :
                                                job.status === "failed" ? "bg-red-500/10 text-red-400" :
                                                job.status === "running" ? "bg-yellow-500/10 text-yellow-400" :
                                                job.status === "partial_pass" ? "bg-amber-500/10 text-amber-400 border border-amber-500/20" :
                                                job.status === "human_review" ? "bg-purple-500/10 text-purple-400 border border-purple-500/20" :
                                                "bg-zinc-500/10 text-zinc-400"
                                            }`}>
                                                {job.status === "partial_pass" ? "Partial Pass" :
                                                 job.status === "human_review" ? "Human Review" :
                                                 job.status}
                                            </span>
                                            {job.is_audited && (
                                                <span className="flex items-center gap-1 text-xs text-purple-400">
                                                    <Shield className="w-3 h-3" />
                                                    Audited
                                                </span>
                                            )}
                                            {/* Retry indicator */}
                                            {job.retry_count > 0 && (
                                                <span className="flex items-center gap-1 text-xs text-orange-400 bg-orange-500/10 px-1.5 py-0.5 rounded">
                                                    <RefreshCw className="w-3 h-3" />
                                                    Retry {job.retry_count}/{job.max_retries || 3}
                                                </span>
                                            )}
                                            {/* Fallback indicator */}
                                            {job.used_fallback && (
                                                <span className="flex items-center gap-1 text-xs text-cyan-400 bg-cyan-500/10 px-1.5 py-0.5 rounded">
                                                    <ArrowDownCircle className="w-3 h-3" />
                                                    Fallback
                                                </span>
                                            )}
                                        </div>
                                        <p className="text-sm text-zinc-300 font-mono truncate">
                                            {job.test_command}
                                        </p>
                                        <div className="flex items-center gap-4 mt-2 text-xs text-zinc-500 flex-wrap">
                                            <span>Bounty: {job.bounty_id.slice(0, 8)}...</span>
                                            <span>Mode: {job.execution_mode.replace("_", " ")}</span>
                                            {job.exit_code !== null && (
                                                <span className={job.exit_code === 0 ? "text-green-400" : "text-red-400"}>
                                                    Exit: {job.exit_code}
                                                </span>
                                            )}
                                        </div>
                                    </div>
                                    <div className="text-right text-xs text-zinc-500">
                                        <p>{new Date(job.created_at).toLocaleDateString()}</p>
                                        <p>{new Date(job.created_at).toLocaleTimeString()}</p>
                                        {job.completed_at && job.started_at && (
                                            <p className="text-zinc-600 mt-1">
                                                {Math.round((new Date(job.completed_at).getTime() - new Date(job.started_at).getTime()) / 1000)}s
                                            </p>
                                        )}
                                    </div>
                                </div>

                                {/* Failure Reason */}
                                {job.failure_reason && (
                                    <div className="mt-3 pt-3 border-t border-zinc-700/50 flex items-start gap-2">
                                        <AlertTriangle className={`w-4 h-4 mt-0.5 flex-shrink-0 ${
                                            job.failure_severity === "critical" ? "text-red-400" :
                                            job.failure_severity === "warning" ? "text-yellow-400" :
                                            "text-zinc-400"
                                        }`} />
                                        <div>
                                            <span className="text-xs text-zinc-400">Failure Reason: </span>
                                            <span className={`text-xs ${
                                                job.failure_severity === "critical" ? "text-red-400" :
                                                job.failure_severity === "warning" ? "text-yellow-400" :
                                                "text-zinc-300"
                                            }`}>
                                                {job.failure_reason}
                                            </span>
                                        </div>
                                    </div>
                                )}

                                {/* Test Results & Pass Status */}
                                {job.passed !== null && (
                                    <div className="mt-3 pt-3 border-t border-zinc-700/50 flex items-center gap-2 flex-wrap">
                                        {job.passed ? (
                                            <>
                                                <CheckCircle className="w-4 h-4 text-green-400" />
                                                <span className="text-sm text-green-400">Tests Passed</span>
                                            </>
                                        ) : (
                                            <>
                                                <XCircle className="w-4 h-4 text-red-400" />
                                                <span className="text-sm text-red-400">Tests Failed</span>
                                            </>
                                        )}
                                        {/* Test counts */}
                                        {job.total_tests > 0 && (
                                            <span className="text-xs text-zinc-400 ml-2">
                                                ({job.passed_tests}/{job.total_tests} passed)
                                            </span>
                                        )}
                                        {job.audit_result && (
                                            <span className={`text-xs ml-auto ${
                                                job.audit_result === "passed" ? "text-green-400" : "text-red-400"
                                            }`}>
                                                Audit: {job.audit_result}
                                            </span>
                                        )}
                                    </div>
                                )}

                                {/* Human Review Badge */}
                                {job.status === "human_review" && (
                                    <div className="mt-3 pt-3 border-t border-zinc-700/50 flex items-center gap-2">
                                        <UserCheck className="w-4 h-4 text-purple-400" />
                                        <span className="text-sm text-purple-400">
                                            Awaiting manual review
                                        </span>
                                    </div>
                                )}
                            </div>
                        ))}
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
