"use client";

import { useState, useEffect } from "react";
import {
  Plus,
  Target,
  DollarSign,
  Briefcase,
  CheckCircle,
  Clock,
  User,
  Bot,
  GitBranch,
  Layers,
  Zap,
  LayoutGrid,
  List
} from "lucide-react";
import DAGVisualization from "../components/DAGVisualization";
import HierarchicalTaskForm from "../components/HierarchicalTaskForm";
import PreparationMode from "../components/PreparationMode";
import ParallelTracksView from "../components/ParallelTracksView";
import AgentWorkloadPanel from "../components/AgentWorkloadPanel";
import CollaborationPanel from "../components/CollaborationPanel";
import CodeReviewPanel from "../components/CodeReviewPanel";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "/api";
const AGENT_API_KEY = process.env.NEXT_PUBLIC_AGENT_API_KEY || "";
const AGENT_ID = process.env.NEXT_PUBLIC_AGENT_ID || "";

type StatusFilter = "all" | "pending" | "ready_for_preparation" | "open" | "in_progress" | "submitted" | "completed";
type ViewMode = "list" | "tracks" | "dag";

interface Bounty {
  id: string;
  title: string;
  description: string;
  reward: number;
  status: string;
  repo_name: string;
  required_role: string;
  assignee?: string;
  dependencies?: string[];
  track?: string;
  estimated_hours?: number;
  parent_id?: string;
}

export default function BountiesPage() {
    const [bounties, setBounties] = useState<Bounty[]>([]);
    const [loading, setLoading] = useState(false);
    const [filter, setFilter] = useState<StatusFilter>("all");
    const [viewMode, setViewMode] = useState<ViewMode>("list");

    // Form State
    const [title, setTitle] = useState("");
    const [desc, setDesc] = useState("");
    const [amount, setAmount] = useState(100);
    const [repo, setRepo] = useState("");
    const [role, setRole] = useState("contributor");
    const [verificationMode, setVerificationMode] = useState("auto");

    // Hierarchical form state
    const [showHierarchicalForm, setShowHierarchicalForm] = useState(false);

    useEffect(() => {
        fetchBounties();
    }, []);

    async function fetchBounties() {
        try {
            const res = await fetch(`${API_BASE}/bounties`, {
                headers: AGENT_API_KEY ? { "X-API-Key": AGENT_API_KEY } : undefined
            });
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
            required_role: role,
            verification_mode: verificationMode
        };

        await fetch(`${API_BASE}/bounties`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                ...(AGENT_API_KEY ? { "X-API-Key": AGENT_API_KEY } : {})
            },
            body: JSON.stringify(payload)
        });

        setLoading(false);
        setTitle("");
        setDesc("");
        setRepo("");
        fetchBounties();
    }

    async function handleHierarchicalSubmit(rootTask: any, repoName: string) {
        setLoading(true);
        const finalRepoName = repoName.endsWith('.git') ? repoName : `${repoName}.git`;

        try {
            const res = await fetch(`${API_BASE}/v1/bounties/decomposed`, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    ...(AGENT_API_KEY ? { "X-API-Key": AGENT_API_KEY } : {})
                },
                body: JSON.stringify({
                    repo_name: finalRepoName,
                    root_task: rootTask
                })
            });

            if (res.ok) {
                const data = await res.json();
                console.log("Created", data.total_created, "bounties");
                setShowHierarchicalForm(false);
                fetchBounties();
            } else {
                const error = await res.json();
                alert(error.detail || "Failed to create task tree");
            }
        } catch (e) {
            console.error(e);
            alert("Failed to create task tree");
        } finally {
            setLoading(false);
        }
    }

    async function handleClaimPreparation(bountyId: string, notes: string) {
        const res = await fetch(`${API_BASE}/v1/bounties/${bountyId}/claim-preparation`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                ...(AGENT_API_KEY ? { "X-API-Key": AGENT_API_KEY } : {})
            },
            body: JSON.stringify({
                agent_id: AGENT_ID,
                preparation_notes: notes
            })
        });

        if (res.ok) {
            fetchBounties();
        } else {
            const error = await res.json();
            alert(error.detail || "Failed to claim for preparation");
        }
    }

    async function handleActivate(bountyId: string) {
        const res = await fetch(`${API_BASE}/v1/bounties/${bountyId}/activate-from-preparation`, {
            method: "POST",
            headers: {
                ...(AGENT_API_KEY ? { "X-API-Key": AGENT_API_KEY } : {})
            }
        });

        if (res.ok) {
            fetchBounties();
        } else {
            const error = await res.json();
            alert(error.detail || "Failed to activate");
        }
    }

    const filteredBounties = bounties.filter(b =>
        filter === "all" || b.status === filter
    );

    const statusCounts = {
        all: bounties.length,
        pending: bounties.filter(b => b.status === "pending").length,
        ready_for_preparation: bounties.filter(b => b.status === "ready_for_preparation").length,
        open: bounties.filter(b => b.status === "open").length,
        in_progress: bounties.filter(b => b.status === "in_progress").length,
        submitted: bounties.filter(b => b.status === "submitted").length,
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

            {/* View Mode Toggle */}
            <div className="flex items-center gap-4">
                <div className="flex gap-1 bg-zinc-900 p-1 rounded-lg">
                    <button
                        onClick={() => setViewMode("list")}
                        className={`flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium transition-colors ${viewMode === "list" ? "bg-zinc-700 text-white" : "text-zinc-400 hover:text-white"}`}
                    >
                        <List className="w-3.5 h-3.5" /> List
                    </button>
                    <button
                        onClick={() => setViewMode("tracks")}
                        className={`flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium transition-colors ${viewMode === "tracks" ? "bg-zinc-700 text-white" : "text-zinc-400 hover:text-white"}`}
                    >
                        <Layers className="w-3.5 h-3.5" /> Tracks
                    </button>
                    <button
                        onClick={() => setViewMode("dag")}
                        className={`flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium transition-colors ${viewMode === "dag" ? "bg-zinc-700 text-white" : "text-zinc-400 hover:text-white"}`}
                    >
                        <GitBranch className="w-3.5 h-3.5" /> DAG
                    </button>
                </div>

                {/* Preparation Mode indicator */}
                {statusCounts.ready_for_preparation > 0 && (
                    <div className="flex items-center gap-2 text-xs text-orange-400 bg-orange-500/10 px-2 py-1 rounded">
                        <Zap className="w-3 h-3" />
                        {statusCounts.ready_for_preparation} task(s) ready for preparation
                    </div>
                )}
            </div>

            {/* Status Tabs */}
            <div className="flex gap-2 flex-wrap">
                {(["all", "pending", "ready_for_preparation", "open", "in_progress", "submitted", "completed"] as StatusFilter[]).map(status => (
                    <button
                        key={status}
                        onClick={() => setFilter(status)}
                        className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-all flex items-center gap-2 ${filter === status
                                ? "bg-yellow-500/20 text-yellow-400 border border-yellow-500/30"
                                : "bg-zinc-900 text-zinc-400 hover:bg-zinc-800 border border-zinc-800"
                            }`}
                    >
                        {status === "open" && <Clock className="w-3 h-3" />}
                        {status === "in_progress" && <Bot className="w-3 h-3" />}
                        {status === "submitted" && <Clock className="w-3 h-3" />}
                        {status === "completed" && <CheckCircle className="w-3 h-3" />}
                        {status === "pending" && <Clock className="w-3 h-3" />}
                        {status === "ready_for_preparation" && <Zap className="w-3 h-3" />}
                        <span className="capitalize">{status.replace("_", " ")}</span>
                        <span className="text-xs opacity-60">({statusCounts[status]})</span>
                    </button>
                ))}
            </div>

            {/* Main Content Area */}
            <div className="flex gap-6">
                {/* Left: Bounties View */}
                <div className="flex-1 space-y-4">
                    {/* DAG View */}
                    {viewMode === "dag" && (
                        <DAGVisualization
                            bounties={bounties.map(b => ({ ...b, dependencies: b.dependencies || [] }))}
                            onNodeClick={(id) => console.log("Clicked", id)}
                        />
                    )}

                    {/* Tracks View */}
                    {viewMode === "tracks" && (
                        <ParallelTracksView
                            bounties={filteredBounties.map(b => ({ ...b, dependencies: b.dependencies || [] }))}
                            onTaskClick={(id) => console.log("Clicked", id)}
                        />
                    )}

                    {/* List View */}
                    {viewMode === "list" && (
                        <>
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
                                            <div className="flex items-center gap-2 mb-1 flex-wrap">
                                                <h3 className="text-lg font-bold text-zinc-200">{b.title}</h3>
                                                <span className={`text-xs px-2 py-0.5 rounded-full uppercase font-medium ${b.status === 'open'
                                                        ? 'bg-green-500/10 text-green-400 border border-green-500/20'
                                                        : b.status === 'in_progress'
                                                            ? 'bg-blue-500/10 text-blue-400 border border-blue-500/20'
                                                            : b.status === 'submitted'
                                                                ? 'bg-yellow-500/10 text-yellow-400 border border-yellow-500/20'
                                                                : b.status === 'ready_for_preparation'
                                                                    ? 'bg-orange-500/10 text-orange-400 border border-orange-500/20'
                                                                    : b.status === 'pending'
                                                                        ? 'bg-zinc-500/10 text-zinc-400 border border-zinc-500/20'
                                                                        : 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                                                    }`}>
                                                    {b.status.replace("_", " ")}
                                                </span>
                                                {b.track && (
                                                    <span className="text-xs bg-purple-500/10 text-purple-400 px-2 py-0.5 rounded">
                                                        {b.track}
                                                    </span>
                                                )}
                                            </div>
                                            <p className="text-sm text-zinc-400 line-clamp-2">{b.description}</p>
                                            <div className="flex gap-2 mt-3 flex-wrap">
                                                <span className="text-xs px-2 py-0.5 rounded bg-zinc-800 text-zinc-400">
                                                    {b.repo_name}
                                                </span>
                                                <span className="text-xs px-2 py-0.5 rounded bg-purple-500/10 text-purple-400">
                                                    needs: {b.required_role}
                                                </span>
                                                {b.estimated_hours && (
                                                    <span className="text-xs px-2 py-0.5 rounded bg-blue-500/10 text-blue-400 flex items-center gap-1">
                                                        <Clock className="w-3 h-3" />
                                                        {b.estimated_hours}h
                                                    </span>
                                                )}
                                            </div>
                                            {b.assignee && (
                                                <div className="text-xs text-zinc-500 mt-2 flex items-center gap-1">
                                                    <Bot className="w-3 h-3" /> Claimed by: <span className="text-blue-400">{b.assignee}</span>
                                                </div>
                                            )}
                                            {b.dependencies && b.dependencies.length > 0 && (
                                                <div className="text-xs text-zinc-600 mt-1 flex items-center gap-1">
                                                    <GitBranch className="w-3 h-3" />
                                                    Depends on {b.dependencies.length} task(s)
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
                        </>
                    )}
                </div>

                {/* Right: Forms Panel */}
                <div className="w-80 space-y-4 sticky top-24">
                    {/* Hierarchical Task Form */}
                    {showHierarchicalForm ? (
                        <HierarchicalTaskForm
                            onSubmit={handleHierarchicalSubmit}
                            existingTasks={bounties.map(b => ({ id: b.id, title: b.title }))}
                            onCancel={() => setShowHierarchicalForm(false)}
                        />
                    ) : (
                        <>
                            {/* Quick Task Toggle */}
                            <button
                                onClick={() => setShowHierarchicalForm(true)}
                                className="w-full glass-panel rounded-xl p-4 text-left hover:border-purple-500/30 transition-colors group"
                            >
                                <div className="flex items-center justify-between">
                                    <div className="flex items-center gap-2">
                                        <GitBranch className="w-5 h-5 text-purple-400" />
                                        <span className="font-medium text-white">Create Task Tree</span>
                                    </div>
                                    <Plus className="w-4 h-4 text-zinc-500 group-hover:text-purple-400 transition-colors" />
                                </div>
                                <p className="text-xs text-zinc-500 mt-2">
                                    Create hierarchical tasks with dependencies and parallel tracks
                                </p>
                            </button>

                            {/* Preparation Mode Panel */}
                            {(statusCounts.ready_for_preparation > 0 || statusCounts.pending > 0) && (
                                <PreparationMode
                                    bounties={bounties.map(b => ({ ...b, dependencies: b.dependencies || [] }))}
                                    onClaimPreparation={handleClaimPreparation}
                                    onActivate={handleActivate}
                                    agentId={AGENT_ID}
                                />
                            )}

                            {/* Agent Workload Panel */}
                            <AgentWorkloadPanel />

                            {/* Collaboration Panel */}
                            <CollaborationPanel />

                            {/* Code Review Panel */}
                            <CodeReviewPanel />

                            {/* Simple Post Form */}
                            <div className="glass-panel rounded-xl p-6">
                                <h2 className="text-lg font-bold text-white mb-4 flex items-center gap-2">
                                    <Briefcase className="w-5 h-5 text-yellow-500" />
                                    Quick Task
                                </h2>
                                <p className="text-xs text-zinc-500 mb-4">
                                    Simple single task. For complex features, use Task Tree above.
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
                                        <label className="text-xs text-zinc-500 uppercase">Verification Mode</label>
                                        <select
                                            value={verificationMode}
                                            onChange={e => setVerificationMode(e.target.value)}
                                            className="w-full bg-black/50 border border-zinc-800 rounded px-3 py-2 text-sm text-white focus:border-yellow-500/50 focus:outline-none transition-colors"
                                        >
                                            <option value="auto">Auto (Sandbox)</option>
                                            <option value="human">Human Verify</option>
                                            <option value="external">External CI</option>
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
                        </>
                    )}
                </div>
            </div>
        </div>
    );
}
