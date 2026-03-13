"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import {
    GitBranch, Plus, Search, Settings, Users, Target,
    ExternalLink, Crown, Loader2, LogIn
} from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "/api";

interface Repo {
    id: string;
    full_name: string;
    name: string;
    owner: string;
    description: string | null;
    member_count: number;
    bounty_count: number;
    is_member: boolean;
    your_role: string | null;
    is_owner: boolean;
    created_at: string;
}

export default function ReposPage() {
    const router = useRouter();
    const [repos, setRepos] = useState<Repo[]>([]);
    const [loading, setLoading] = useState(true);
    const [showCreate, setShowCreate] = useState(false);
    const [mineOnly, setMineOnly] = useState(false);
    const [search, setSearch] = useState("");
    const [isLoggedIn, setIsLoggedIn] = useState(false);

    // Create form
    const [newFullName, setNewFullName] = useState("");
    const [newDescription, setNewDescription] = useState("");
    const [creating, setCreating] = useState(false);
    const [error, setError] = useState("");

    // Check login status (but don't block viewing)
    useEffect(() => {
        const token = localStorage.getItem("token");
        setIsLoggedIn(!!token);
    }, []);

    useEffect(() => {
        fetchRepos();
    }, [mineOnly]);

    const getAuthHeaders = () => {
        const token = typeof window !== 'undefined' ? localStorage.getItem("token") : null;
        const headers: Record<string, string> = {
            "Content-Type": "application/json",
        };
        if (token) {
            headers["Authorization"] = `Bearer ${token}`;
        }
        return headers;
    };

    const fetchRepos = async () => {
        try {
            setLoading(true);
            const params = new URLSearchParams();
            if (mineOnly) params.set("mine", "true");

            const res = await fetch(`${API_BASE}/api/v1/repos?${params}`, {
                headers: getAuthHeaders()
            });
            if (res.ok) {
                setRepos(await res.json());
            }
        } catch (e) {
            console.error(e);
        } finally {
            setLoading(false);
        }
    };

    const requireAuth = () => {
        if (!isLoggedIn) {
            router.push("/login");
            return false;
        }
        return true;
    };

    const handleCreateClick = () => {
        if (!requireAuth()) return;
        setShowCreate(true);
    };

    const handleCreate = async () => {
        if (!requireAuth()) return;

        if (!newFullName.includes("/")) {
            setError("Format must be owner/repo");
            return;
        }

        try {
            setCreating(true);
            setError("");

            const res = await fetch(`${API_BASE}/api/v1/repos`, {
                method: "POST",
                headers: getAuthHeaders(),
                body: JSON.stringify({
                    full_name: newFullName,
                    description: newDescription,
                    is_private: false
                })
            });

            const data = await res.json();

            if (!res.ok) {
                setError(data.detail || "Failed to create repo");
                return;
            }

            setShowCreate(false);
            setNewFullName("");
            setNewDescription("");
            fetchRepos();

        } catch (e) {
            setError("Network error");
        } finally {
            setCreating(false);
        }
    };

    const filteredRepos = repos.filter(r =>
        r.full_name.toLowerCase().includes(search.toLowerCase()) ||
        r.description?.toLowerCase().includes(search.toLowerCase())
    );

    const getRoleIcon = (role: string | null) => {
        if (role === "architect") return <Crown className="w-4 h-4 text-purple-400" />;
        return null;
    };

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="flex items-start justify-between">
                <div>
                    <h1 className="text-3xl font-bold text-white flex items-center gap-3">
                        <GitBranch className="w-8 h-8 text-emerald-500" />
                        Repositories
                    </h1>
                    <p className="text-zinc-400 mt-2 max-w-xl">
                        Browse repositories. {!isLoggedIn && <span className="text-emerald-400">Login to create and manage.</span>}
                    </p>
                </div>
                {isLoggedIn ? (
                    <button
                        onClick={handleCreateClick}
                        className="px-4 py-2 bg-emerald-500 hover:bg-emerald-600 text-white font-medium rounded-lg flex items-center gap-2 transition-colors"
                    >
                        <Plus className="w-4 h-4" />
                        New Repository
                    </button>
                ) : (
                    <button
                        onClick={() => router.push("/login")}
                        className="px-4 py-2 bg-zinc-700 hover:bg-zinc-600 text-white font-medium rounded-lg flex items-center gap-2 transition-colors"
                    >
                        <LogIn className="w-4 h-4" />
                        Login to Create
                    </button>
                )}
            </div>

            {/* Filters */}
            <div className="flex items-center gap-4">
                <div className="relative flex-1 max-w-md">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-500" />
                    <input
                        type="text"
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        placeholder="Search repositories..."
                        className="w-full bg-zinc-900/50 border border-zinc-800 rounded-lg py-2 pl-10 pr-4 text-white placeholder-zinc-600 focus:border-emerald-500/50 focus:outline-none"
                    />
                </div>
                {isLoggedIn && (
                    <label className="flex items-center gap-2 text-sm text-zinc-400">
                        <input
                            type="checkbox"
                            checked={mineOnly}
                            onChange={(e) => setMineOnly(e.target.checked)}
                            className="rounded"
                        />
                        Only my repos
                    </label>
                )}
            </div>

            {/* Create Modal */}
            {showCreate && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
                    <div className="glass-panel rounded-2xl p-6 max-w-md w-full mx-4">
                        <h2 className="text-xl font-bold text-white mb-4 flex items-center gap-2">
                            <GitBranch className="w-5 h-5 text-emerald-400" />
                            New Repository
                        </h2>
                        <p className="text-sm text-zinc-400 mb-4">
                            Only Architects can create new repositories. You will automatically become the Architect.
                        </p>

                        {error && (
                            <div className="mb-4 px-3 py-2 bg-red-500/10 border border-red-500/20 rounded-lg text-red-400 text-sm">
                                {error}
                            </div>
                        )}

                        <div className="space-y-4">
                            <div>
                                <label className="text-sm text-zinc-400">Repository Name</label>
                                <input
                                    type="text"
                                    value={newFullName}
                                    onChange={(e) => setNewFullName(e.target.value)}
                                    placeholder="owner/repo"
                                    className="w-full mt-1 bg-zinc-800 border border-zinc-700 rounded-lg px-4 py-2 text-white placeholder-zinc-500 focus:border-emerald-500/50 focus:outline-none"
                                />
                            </div>
                            <div>
                                <label className="text-sm text-zinc-400">Description (optional)</label>
                                <input
                                    type="text"
                                    value={newDescription}
                                    onChange={(e) => setNewDescription(e.target.value)}
                                    placeholder="Brief description..."
                                    className="w-full mt-1 bg-zinc-800 border border-zinc-700 rounded-lg px-4 py-2 text-white placeholder-zinc-500 focus:border-emerald-500/50 focus:outline-none"
                                />
                            </div>
                        </div>

                        <div className="flex gap-3 mt-6">
                            <button
                                onClick={() => setShowCreate(false)}
                                className="flex-1 px-4 py-2 bg-zinc-700 hover:bg-zinc-600 text-white rounded-lg"
                            >
                                Cancel
                            </button>
                            <button
                                onClick={handleCreate}
                                disabled={creating || !newFullName}
                                className="flex-1 px-4 py-2 bg-emerald-500 hover:bg-emerald-600 disabled:bg-zinc-700 text-white rounded-lg flex items-center justify-center gap-2"
                            >
                                {creating ? (
                                    <Loader2 className="w-4 h-4 animate-spin" />
                                ) : (
                                    "Create"
                                )}
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* Repos List */}
            {loading ? (
                <div className="text-center py-16">
                    <div className="animate-pulse flex flex-col items-center gap-4">
                        <GitBranch className="w-12 h-12 text-zinc-700" />
                        <p className="text-zinc-500">Loading...</p>
                    </div>
                </div>
            ) : filteredRepos.length === 0 ? (
                <div className="glass-panel rounded-xl p-12 text-center">
                    <GitBranch className="w-16 h-16 text-zinc-600 mx-auto mb-4" />
                    <h3 className="text-xl font-semibold text-white mb-2">
                        {mineOnly ? "No repositories yet" : "No repositories found"}
                    </h3>
                    <p className="text-zinc-400 mb-6">
                        {mineOnly
                            ? "Create your first repository to get started"
                            : "Try adjusting your search or filters"
                        }
                    </p>
                </div>
            ) : (
                <div className="grid gap-4">
                    {filteredRepos.map((repo) => (
                        <div
                            key={repo.id}
                            className="glass-panel rounded-xl p-5 transition-all hover:border-emerald-500/30"
                        >
                            <div className="flex items-start justify-between">
                                <div className="flex items-start gap-4">
                                    <div className="w-12 h-12 rounded-xl bg-zinc-800 flex items-center justify-center">
                                        <GitBranch className="w-6 h-6 text-zinc-400" />
                                    </div>
                                    <div>
                                        <div className="flex items-center gap-2">
                                            <h3 className="text-lg font-semibold text-white hover:text-emerald-400 cursor-pointer"
                                                onClick={() => router.push(`/repos/${repo.id}`)}>
                                                {repo.full_name}
                                            </h3>
                                            {repo.is_owner && (
                                                <span className="text-xs px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                                                    Owner
                                                </span>
                                            )}
                                            {repo.your_role && getRoleIcon(repo.your_role)}
                                        </div>
                                        <p className="text-sm text-zinc-400 mt-1">
                                            {repo.description || "No description"}
                                        </p>
                                        <div className="flex items-center gap-4 mt-2 text-xs text-zinc-500">
                                            <span className="flex items-center gap-1">
                                                <Users className="w-3 h-3" />
                                                {repo.member_count} members
                                            </span>
                                            <span className="flex items-center gap-1">
                                                <Target className="w-3 h-3" />
                                                {repo.bounty_count} bounties
                                            </span>
                                            <span className="flex items-center gap-1">
                                                {repo.your_role ? (
                                                    <span className="text-emerald-400">{repo.your_role}</span>
                                                ) : (
                                                    <span className="text-zinc-500">Not joined</span>
                                                )}
                                            </span>
                                        </div>
                                    </div>
                                </div>
                                <div className="flex items-center gap-2">
                                    <button
                                        onClick={() => router.push(`/repos/${repo.id}/settings`)}
                                        className="p-2 text-zinc-500 hover:text-white hover:bg-zinc-800 rounded-lg transition-colors"
                                        title="Settings"
                                    >
                                        <Settings className="w-5 h-5" />
                                    </button>
                                    <a
                                        href={`https://github.com/${repo.full_name}`}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="p-2 text-zinc-500 hover:text-white hover:bg-zinc-800 rounded-lg transition-colors"
                                        title="Open in GitHub"
                                    >
                                        <ExternalLink className="w-5 h-5" />
                                    </a>
                                </div>
                            </div>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}
