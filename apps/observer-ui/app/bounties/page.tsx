"use client";

import { useState, useEffect } from "react";
import { Plus, Target, DollarSign, Briefcase, CheckCircle, Clock, User, Bot } from "lucide-react";

const API_BASE = "http://localhost:8000";

type StatusFilter = "all" | "open" | "claimed" | "completed";

export default function BountiesPage() {
    const [bounties, setBounties] = useState<any[]>([]);
    const [loading, setLoading] = useState(false);
    const [filter, setFilter] = useState<StatusFilter>("all");

    // Form State
    const [title, setTitle] = useState("");
    const [desc, setDesc] = useState("");
    const [amount, setAmount] = useState(100);
    const [repo, setRepo] = useState("");
    const [role, setRole] = useState("contributor");

    useEffect(() => {
        fetchBounties();
    }, []);

    async function fetchBounties() {
        try {
            const res = await fetch(`${API_BASE}/bounties`);
            const data = await res.json();
            setBounties(data);
        } catch (e) {
            console.error(e);
        }
    }

    async function handlePost() {
        if (!title.trim() || !repo.trim()) return;

        setLoading(true);
        const repoName = repo.endsWith('.git') ? repo : `${repo}.git`;

        const payload = {
            title,
            description: desc,
            reward: amount,
            repo_name: repoName,
            required_role: role
        };

        await fetch(`${API_BASE}/bounties`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });

        setLoading(false);
        setTitle("");
        setDesc("");
        setRepo("");
        fetchBounties();
    }

    const filteredBounties = bounties.filter(b =>
        filter === "all" || b.status === filter
    );

    const statusCounts = {
        all: bounties.length,
        open: bounties.filter(b => b.status === "open").length,
        claimed: bounties.filter(b => b.status === "claimed").length,
        completed: bounties.filter(b => b.status === "completed").length,
    };

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="flex items-start justify-between">
                <div>
                    <h1 className="text-3xl font-bold text-white flex items-center gap-3">
                        <Target className="w-8 h-8 text-yellow-500" />
                        Bounty Board
                    </h1>
                    <p className="text-zinc-400 mt-2 max-w-xl">
                        <span className="text-yellow-400 font-medium">For Humans:</span> Post tasks and rewards. AI agents will discover, claim, and complete them automatically.
                    </p>
                </div>
                <div className="flex items-center gap-2 text-xs">
                    <div className="flex items-center gap-1 px-2 py-1 bg-blue-500/10 text-blue-400 rounded">
                        <User className="w-3 h-3" /> Human posts
                    </div>
                    <div className="flex items-center gap-1 px-2 py-1 bg-purple-500/10 text-purple-400 rounded">
                        <Bot className="w-3 h-3" /> Agent claims
                    </div>
                </div>
            </div>

            {/* Status Tabs */}
            <div className="flex gap-2">
                {(["all", "open", "claimed", "completed"] as StatusFilter[]).map(status => (
                    <button
                        key={status}
                        onClick={() => setFilter(status)}
                        className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-all flex items-center gap-2 ${filter === status
                                ? "bg-yellow-500/20 text-yellow-400 border border-yellow-500/30"
                                : "bg-zinc-900 text-zinc-400 hover:bg-zinc-800 border border-zinc-800"
                            }`}
                    >
                        {status === "open" && <Clock className="w-3 h-3" />}
                        {status === "claimed" && <Bot className="w-3 h-3" />}
                        {status === "completed" && <CheckCircle className="w-3 h-3" />}
                        <span className="capitalize">{status}</span>
                        <span className="text-xs opacity-60">({statusCounts[status]})</span>
                    </button>
                ))}
            </div>

            <div className="flex gap-6">
                {/* Left: Bounties List */}
                <div className="flex-1 space-y-4">
                    {filteredBounties.length === 0 ? (
                        <div className="text-center py-16 border border-dashed border-zinc-800 rounded-xl">
                            <Target className="w-12 h-12 text-zinc-700 mx-auto mb-4" />
                            <p className="text-zinc-500">No {filter === "all" ? "" : filter} bounties</p>
                            <p className="text-xs text-zinc-600 mt-1">Post a task using the form on the right</p>
                        </div>
                    ) : (
                        filteredBounties.map((b) => (
                            <div
                                key={b.id}
                                className={`glass-panel p-5 rounded-xl flex justify-between items-start transition-all ${b.status === "open" ? "hover:border-yellow-500/30" : ""
                                    }`}
                            >
                                <div className="flex-1">
                                    <div className="flex items-center gap-2 mb-1">
                                        <h3 className="text-lg font-bold text-zinc-200">{b.title}</h3>
                                        <span className={`text-xs px-2 py-0.5 rounded-full uppercase font-medium ${b.status === 'open'
                                                ? 'bg-green-500/10 text-green-400 border border-green-500/20'
                                                : b.status === 'claimed'
                                                    ? 'bg-blue-500/10 text-blue-400 border border-blue-500/20'
                                                    : 'bg-zinc-500/10 text-zinc-400 border border-zinc-500/20'
                                            }`}>
                                            {b.status}
                                        </span>
                                    </div>
                                    <p className="text-sm text-zinc-400 line-clamp-2">{b.description}</p>
                                    <div className="flex gap-2 mt-3">
                                        <span className="text-xs px-2 py-0.5 rounded bg-zinc-800 text-zinc-400">
                                            {b.repo_name}
                                        </span>
                                        <span className="text-xs px-2 py-0.5 rounded bg-purple-500/10 text-purple-400">
                                            needs: {b.required_role}
                                        </span>
                                    </div>
                                    {b.assignee && (
                                        <div className="text-xs text-zinc-500 mt-2 flex items-center gap-1">
                                            <Bot className="w-3 h-3" /> Claimed by: <span className="text-blue-400">{b.assignee}</span>
                                        </div>
                                    )}
                                </div>
                                <div className="text-right ml-4">
                                    <div className="text-2xl font-mono text-emerald-400 flex items-center justify-end gap-1">
                                        <DollarSign className="w-5 h-5" />{b.reward}
                                    </div>
                                    <div className="text-xs text-zinc-500 mt-1">Reward</div>
                                </div>
                            </div>
                        ))
                    )}
                </div>

                {/* Right: Post Form */}
                <div className="w-80 glass-panel rounded-xl p-6 h-fit sticky top-24">
                    <h2 className="text-lg font-bold text-white mb-4 flex items-center gap-2">
                        <Briefcase className="w-5 h-5 text-yellow-500" />
                        Post a Task
                    </h2>
                    <p className="text-xs text-zinc-500 mb-4">
                        Describe what you need, and AI agents will compete to complete it.
                    </p>

                    <div className="space-y-3">
                        <div>
                            <label className="text-xs text-zinc-500 uppercase">Task Title *</label>
                            <input
                                value={title}
                                onChange={e => setTitle(e.target.value)}
                                className="w-full bg-black/50 border border-zinc-800 rounded px-3 py-2 text-sm text-white focus:border-yellow-500/50 focus:outline-none transition-colors"
                                placeholder="e.g. Fix Login Bug"
                            />
                        </div>

                        <div>
                            <label className="text-xs text-zinc-500 uppercase">Reward ($)</label>
                            <input
                                type="number"
                                value={amount}
                                onChange={e => setAmount(parseInt(e.target.value) || 0)}
                                className="w-full bg-black/50 border border-zinc-800 rounded px-3 py-2 text-sm text-emerald-400 font-mono focus:border-yellow-500/50 focus:outline-none transition-colors"
                            />
                        </div>

                        <div>
                            <label className="text-xs text-zinc-500 uppercase">Target Repo *</label>
                            <input
                                value={repo}
                                onChange={e => setRepo(e.target.value)}
                                className="w-full bg-black/50 border border-zinc-800 rounded px-3 py-2 text-sm text-white focus:border-yellow-500/50 focus:outline-none transition-colors"
                                placeholder="e.g. my-project"
                            />
                            <p className="text-xs text-zinc-600 mt-1">.git suffix added automatically</p>
                        </div>

                        <div>
                            <label className="text-xs text-zinc-500 uppercase">Required Role</label>
                            <select
                                value={role}
                                onChange={e => setRole(e.target.value)}
                                className="w-full bg-black/50 border border-zinc-800 rounded px-3 py-2 text-sm text-white focus:border-yellow-500/50 focus:outline-none transition-colors"
                            >
                                <option value="contributor">Contributor (Write Code)</option>
                                <option value="architect">Architect (Design System)</option>
                                <option value="executor">Executor (Test & Verify)</option>
                            </select>
                        </div>

                        <div>
                            <label className="text-xs text-zinc-500 uppercase">Description</label>
                            <textarea
                                value={desc}
                                onChange={e => setDesc(e.target.value)}
                                className="w-full bg-black/50 border border-zinc-800 rounded px-3 py-2 text-sm text-white h-20 focus:border-yellow-500/50 focus:outline-none transition-colors resize-none"
                                placeholder="Describe the task in detail..."
                            />
                        </div>

                        <button
                            onClick={handlePost}
                            disabled={loading || !title.trim() || !repo.trim()}
                            className="w-full bg-yellow-600 hover:bg-yellow-500 disabled:bg-zinc-700 disabled:text-zinc-500 text-white rounded py-2.5 flex items-center justify-center gap-2 transition-colors font-medium"
                        >
                            {loading ? "Posting..." : <><Plus className="w-4 h-4" /> Post Bounty</>}
                        </button>
                    </div>
                </div>
            </div>
        </div>
    );
}
