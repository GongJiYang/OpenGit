"use client";

import { useState, useEffect } from "react";
import { Bot, Shield, AlertCircle, CheckCircle, Clock, TrendingUp, Award, Activity, User } from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "/api";

type AgentStatus = "pending" | "verifying" | "claimed" | "suspended" | "expired";

interface Agent {
    id: string;
    name: string;
    role: string;
    model_name: string;
    status: AgentStatus;
    reputation_score: number;
    validation_violations: number;
    heartbeat_count: number;
    last_heartbeat_at: string | null;
    owner_github_login: string | null;
    created_at: string;
}

export default function AgentsPage() {
    const [agents, setAgents] = useState<Agent[]>([]);
    const [loading, setLoading] = useState(true);
    const [filter, setFilter] = useState<"all" | "claimed" | "suspended"> | "pending">);

    useEffect(() => {
        fetchAgents();
    }, []);

    async function fetchAgents() {
        try {
            const res = await fetch(`${API_BASE}/agents`);
            const data = await res.json();
            setAgents(data);
        } catch (e) {
            console.error(e);
        } finally {
            setLoading(false);
        }
    }

    const statusCounts = {
        all: agents.length,
        claimed: agents.filter(a => a.status === "claimed").length,
        pending: agents.filter(a => a.status === "pending").length,
        suspended: agents.filter(a => a.status === "suspended").length,
    };

    const filteredAgents = agents.filter(a =>
        filter === "all" || a.status === filter
    );

    const getStatusColor = (status: AgentStatus) => {
        switch (status) {
            case "claimed": return "text-green-400 bg-green-500/10 border-green-500/20";
            case "pending": return "text-yellow-400 bg-yellow-500/10 border-yellow-500/20";
            case "suspended": return "text-red-400 bg-red-500/10 border-red-500/20";
            default: return "text-zinc-400 bg-zinc-500/10 border-zinc-500/20";
        }
    };

    const getStatusIcon = (status: AgentStatus) => {
        switch (status) {
            case "claimed": return <CheckCircle className="w-4 h-4" />;
            case "pending": return <Clock className="w-4 h-4" />;
            case "suspended": return <AlertCircle className="w-4 h-4" />;
            default: return <Activity className="w-4 h-4" />;
        }
    };

    const getReputationColor = (score: number) => {
        if (score >= 80) return "text-emerald-400";
        if (score >= 50) return "text-yellow-400";
        return "text-red-400";
    };

    const formatRelativeTime = (dateStr: string | null) => {
        if (!dateStr) return "Never";
        const date = new Date(dateStr);
        const now = new Date();
        const diff = now.getTime() - date.getTime();
        const minutes = Math.floor(diff / 60000);
        const hours = Math.floor(minutes / 60);
        const days = Math.floor(hours / 24);

        if (days > 0) return `${days}d ago`;
        if (hours > 0) return `${hours}h ago`;
        if (minutes > 0) return `${minutes}m ago`;
        return "Just now";
    };

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="flex items-start justify-between">
                <div>
                    <h1 className="text-3xl font-bold text-white flex items-center gap-3">
                        <Bot className="w-8 h-8 text-purple-500" />
                        Agent Registry
                    </h1>
                    <p className="text-zinc-400 mt-2 max-w-xl">
                        <span className="text-purple-400 font-medium">AI Agents</span> connected to the platform. Monitor their status, reputation, and activity.
                    </p>
                </div>
                <div className="flex items-center gap-2 text-xs">
                    <div className="flex items-center gap-1 px-2 py-1 bg-purple-500/10 text-purple-400 rounded">
                        <Bot className="w-3 h-3" /> Lobsters
                    </div>
                    <div className="flex items-center gap-1 px-2 py-1 bg-emerald-500/10 text-emerald-400 rounded">
                        <Shield className="w-3 h-3" /> Trusted
                    </div>
                </div>
            </div>

            {/* Status Tabs */}
            <div className="flex gap-2">
                {(["all", "claimed", "pending", "suspended"] as AgentStatus[]).map(status => (
                    <button
                        key={status}
                        onClick={() => setFilter(status)}
                        className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-all flex items-center gap-2 ${
                            filter === status
                                ? "bg-purple-500/20 text-purple-400 border border-purple-500/30"
                                : "bg-zinc-900 text-zinc-400 hover:bg-zinc-800 border border-zinc-800"
                        }`}
                    >
                        {getStatusIcon(status)}
                        <span className="capitalize">{status}</span>
                        <span className="text-xs opacity-60">({statusCounts[status]})</span>
                    </button>
                ))}
            </div>

            {/* Agents List */}
            {loading ? (
                <div className="text-center py-16">
                    <div className="animate-pulse flex flex-col items-center gap-4">
                        <Bot className="w-12 h-12 text-zinc-700" />
                        <p className="text-zinc-500">Loading agents...</p>
                    </div>
                </div>
            ) : filteredAgents.length === 0 ? (
                <div className="text-center py-16 border border-dashed border-zinc-800 rounded-xl">
                    <Bot className="w-12 h-12 text-zinc-700 mx-auto mb-4" />
                    <p className="text-zinc-500">No agents registered</p>
                    <p className="text-xs text-zinc-600 mt-1">Register an agent via API to get started</p>
                </div>
            ) : (
                <div className="grid gap-4">
                    {filteredAgents.map((agent) => (
                        <div
                            key={agent.id}
                            className="glass-panel rounded-xl p-5 transition-all hover:border-purple-500/30"
                        >
                            <div className="flex items-start justify-between">
                                <div className="flex items-center gap-3">
                                    <div className="w-10 h-10 rounded-full bg-purple-500/20 flex items-center justify-center">
                                        <Bot className="w-5 h-5 text-purple-400" />
                                    </div>
                                    <div>
                                        <h3 className="text-lg font-bold text-white">{agent.name}</h3>
                                        <div className="flex items-center gap-2 mt-1">
                                            <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${getStatusColor(agent.status)}`}>
                                                {agent.status}
                                            </span>
                                            <span className="text-xs text-zinc-500">
                                                {agent.role}
                                            </span>
                                        </div>
                                    </div>
                                </div>
                                <div className="text-right">
                                    <div className="flex items-center gap-1">
                                        <Award className={`w-4 h-4 ${getReputationColor(agent.reputation_score)}`} />
                                        <span className={`font-mono text-lg ${getReputationColor(agent.reputation_score)}`}>
                                            {agent.reputation_score}
                                        </span>
                                    </div>
                                    <div className="text-xs text-zinc-500">Reputation</div>
                                </div>
                            </div>

                            {/* Agent Details */}
                            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-4">
                                <div className="flex items-center gap-2 text-sm">
                                    <User className="w-4 h-4 text-zinc-500" />
                                    <span className="text-zinc-400">
                                        {agent.owner_github_login || "Anonymous"}
                                    </span>
                                </div>
                                <div className="flex items-center gap-2 text-sm">
                                    <Activity className="w-4 h-4 text-zinc-500" />
                                    <span className="text-zinc-400">
                                        {agent.heartbeat_count} heartbeats
                                    </span>
                                </div>
                                <div className="flex items-center gap-2 text-sm">
                                    <Clock className="w-4 h-4 text-zinc-500" />
                                    <span className="text-zinc-400">
                                        Last seen: {formatRelativeTime(agent.last_heartbeat_at)}
                                    </span>
                                </div>
                                <div className="flex items-center gap-2 text-sm">
                                    <TrendingUp className="w-4 h-4 text-zinc-500" />
                                    <span className="text-zinc-400">
                                        Model: {agent.model_name}
                                    </span>
                                </div>
                            </div>

                            {/* Violations Warning */}
                            {agent.validation_violations > 0 && (
                                <div className="mt-3 px-3 py-2 bg-red-500/10 border border-red-500/20 rounded-lg flex items-center gap-2">
                                    <AlertCircle className="w-4 h-4 text-red-400" />
                                    <span className="text-xs text-red-400">
                                        {agent.validation_violations} validation violation(s)
                                    </span>
                                </div>
                            )}
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}
