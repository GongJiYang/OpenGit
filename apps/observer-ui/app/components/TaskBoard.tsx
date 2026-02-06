"use client";

import { useState, useEffect } from "react";
import { Target, DollarSign, Bot, CheckCircle, Clock, Plus, User, Star } from "lucide-react";

interface Task {
    id: string;
    title: string;
    description: string;
    reward: number;
    status: "open" | "claimed" | "completed";
    required_role: string;
    assignee?: string;
    repo_name: string;
    // New fields
    context_files?: string[];
    target_files?: string[];
    acceptance_criteria?: string;
}

const API_BASE = "http://localhost:8000";

export default function TaskBoard({ repoId }: { repoId: string }) {
    const [tasks, setTasks] = useState<Task[]>([]);
    const [loading, setLoading] = useState(true);
    const [filter, setFilter] = useState<"all" | "open" | "claimed" | "completed">("all");

    useEffect(() => {
        fetchTasks();
    }, [repoId]);

    async function fetchTasks() {
        try {
            setLoading(true);
            const res = await fetch(`${API_BASE}/bounties`);
            const data = await res.json();
            const repoTasks = data.filter((t: Task) => t.repo_name === repoId || t.repo_name === `${repoId}.git`);
            setTasks(repoTasks);
        } catch (e) {
            console.error("Failed to fetch tasks", e);
        } finally {
            setLoading(false);
        }
    }

    async function handleClaim(taskId: string) {
        setLoading(true);
        const mockAgentId = "human-user";
        try {
            const res = await fetch(`${API_BASE}/bounties/${taskId}/claim?agent_id=${mockAgentId}`, {
                method: "POST"
            });
            if (res.ok) {
                await fetchTasks();
            }
        } catch (e) {
            console.error("Failed to claim task", e);
        } finally {
            setLoading(false);
        }
    }

    const filteredTasks = tasks.filter(t => filter === "all" || t.status === filter);

    return (
        <div className="glass-panel rounded-2xl">
            {/* Header */}
            <div className="p-4 border-b border-white/5 flex flex-col gap-3">
                <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                        <Target className="w-4 h-4 text-emerald-400" />
                        <h2 className="text-sm font-medium text-zinc-400">Available Tasks</h2>
                    </div>
                    <span className="text-xs text-zinc-600 bg-white/5 px-2 py-0.5 rounded-full">{tasks.length}</span>
                </div>

                {/* Micro Tabs */}
                {tasks.length > 0 && (
                    <div className="flex gap-1">
                        {(["all", "open", "claimed"] as const).map(f => (
                            <button
                                key={f}
                                onClick={() => setFilter(f)}
                                className={`px-2 py-1 rounded text-[10px] font-medium transition-colors uppercase tracking-wider ${filter === f
                                    ? "bg-zinc-800 text-white border border-white/5"
                                    : "text-zinc-600 hover:text-zinc-400 hover:bg-white/5"
                                    }`}
                            >
                                {f}
                            </button>
                        ))}
                    </div>
                )}
            </div>

            {/* List */}
            <div className="divide-y divide-white/5 max-h-[400px] overflow-auto">
                {loading ? (
                    <div className="p-8 text-center text-zinc-600 text-xs animate-pulse">Syncing task board...</div>
                ) : filteredTasks.length === 0 ? (
                    <div className="p-8 text-center">
                        <p className="text-zinc-600 text-xs mb-2">No tasks available</p>
                        <button className="text-xs text-emerald-500 hover:text-emerald-400 transition-colors">
                            + Design new task
                        </button>
                    </div>
                ) : (
                    filteredTasks.map(task => (
                        <div key={task.id} className="p-4 hover:bg-white/5 transition-colors group">
                            <div className="flex items-start justify-between gap-3 mb-2">
                                <h3 className="text-sm font-medium text-zinc-200 line-clamp-1 group-hover:text-emerald-400 transition-colors">
                                    {task.title}
                                </h3>
                                <span className="font-mono text-emerald-500 text-xs font-medium whitespace-nowrap">
                                    ${task.reward}
                                </span>
                            </div>

                            <p className="text-xs text-zinc-500 line-clamp-2 mb-3 leading-relaxed">
                                {task.description}
                            </p>

                            {/* Spec Details */}
                            {((task.context_files && task.context_files.length > 0) || task.acceptance_criteria) && (
                                <div className="mb-3 space-y-2 bg-black/20 p-2 rounded border border-white/5">
                                    {task.context_files && task.context_files.length > 0 && (
                                        <div className="flex gap-2 text-[10px]">
                                            <span className="text-zinc-500 uppercase font-semibold">Context:</span>
                                            <div className="flex gap-1 flex-wrap">
                                                {task.context_files.map(f => (
                                                    <span key={f} className="text-blue-400 bg-blue-500/10 px-1 rounded">{f}</span>
                                                ))}
                                            </div>
                                        </div>
                                    )}
                                    {task.acceptance_criteria && (
                                        <div className="flex gap-2 text-[10px]">
                                            <span className="text-zinc-500 uppercase font-semibold">Verify:</span>
                                            <span className="text-zinc-300 font-mono">{task.acceptance_criteria}</span>
                                        </div>
                                    )}
                                </div>
                            )}

                            <div className="flex items-center justify-between">
                                <div className="flex items-center gap-2">
                                    <span className={`text-[10px] px-1.5 py-0.5 rounded border ${task.status === "open" ? "bg-emerald-500/10 text-emerald-500 border-emerald-500/20" :
                                        task.status === "claimed" ? "bg-blue-500/10 text-blue-500 border-blue-500/20" :
                                            "bg-zinc-500/10 text-zinc-500 border-zinc-500/20"
                                        }`}>
                                        {task.status.toUpperCase()}
                                    </span>
                                    {task.assignee && (
                                        <div className="flex items-center gap-1 text-[10px] text-blue-400/80">
                                            <Bot className="w-3 h-3" /> {task.assignee.slice(0, 8)}..
                                        </div>
                                    )}
                                </div>

                                {task.status === "open" && (
                                    <button
                                        onClick={() => handleClaim(task.id)}
                                        className="text-[10px] bg-zinc-800 hover:bg-emerald-600 hover:text-white text-zinc-400 px-2 py-1 rounded transition-all border border-white/5"
                                    >
                                        Claim
                                    </button>
                                )}
                            </div>
                        </div>
                    ))
                )}
            </div>
        </div>
    );
}
