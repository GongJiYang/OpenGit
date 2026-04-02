"use client";

import { useState, useEffect } from "react";
import {
    RefreshCw, UserCheck, AlertTriangle, CheckCircle, Clock,
    Activity, Loader2, ChevronRight
} from "lucide-react";
import { useRouter } from "next/navigation";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "/api";

interface RecoveryStats {
    pending_retries: number;
    human_review_queue: number;
    partial_passes: number;
    total_in_recovery: number;
}

interface RetryJob {
    id: string;
    bounty_id: string;
    retry_count: number;
    max_retries: number;
    next_retry_at: string | null;
    failure_reason: string | null;
}

interface HumanReviewJob {
    id: string;
    bounty_id: string;
    retry_count: number;
    max_retries: number;
    failure_reason: string | null;
    failure_severity: string | null;
    created_at: string;
    updated_at: string;
    original_runner_id: string | null;
    stdout_preview: string | null;
}

export default function RecoveryStatsPanel() {
    const router = useRouter();
    const [stats, setStats] = useState<RecoveryStats | null>(null);
    const [retryJobs, setRetryJobs] = useState<RetryJob[]>([]);
    const [humanReviewJobs, setHumanReviewJobs] = useState<HumanReviewJob[]>([]);
    const [loading, setLoading] = useState(true);
    const [activeTab, setActiveTab] = useState<"retries" | "review" | "partial">("retries");

    useEffect(() => {
        fetchData();
        // Refresh every 30 seconds
        const interval = setInterval(fetchData, 30000);
        return () => clearInterval(interval);
    }, []);

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

    const fetchData = async () => {
        try {
            // Fetch stats
            const statsRes = await fetch(`${API_BASE}/v1/recovery/stats`, {
                headers: getAuthHeaders()
            });
            if (statsRes.ok) {
                setStats(await statsRes.json());
            }

            // Fetch retry queue
            const retryRes = await fetch(`${API_BASE}/v1/recovery/retry/queue`, {
                headers: getAuthHeaders()
            });
            if (retryRes.ok) {
                const data = await retryRes.json();
                setRetryJobs(data.jobs || []);
            }

            // Fetch human review queue
            const reviewRes = await fetch(`${API_BASE}/v1/recovery/human-review/queue`, {
                headers: getAuthHeaders()
            });
            if (reviewRes.ok) {
                setHumanReviewJobs(await reviewRes.json());
            }
        } catch (e) {
            console.error("Failed to fetch recovery data:", e);
        } finally {
            setLoading(false);
        }
    };

    const formatTimeRemaining = (nextRetryAt: string | null) => {
        if (!nextRetryAt) return "Ready";
        const nextRetry = new Date(nextRetryAt);
        const now = new Date();
        const diff = nextRetry.getTime() - now.getTime();
        if (diff <= 0) return "Ready";
        const mins = Math.floor(diff / 60000);
        const secs = Math.floor((diff % 60000) / 1000);
        if (mins > 0) return `${mins}m ${secs}s`;
        return `${secs}s`;
    };

    if (loading) {
        return (
            <div className="glass-panel rounded-xl p-5">
                <div className="flex items-center justify-center py-8">
                    <Loader2 className="w-6 h-6 text-zinc-500 animate-spin" />
                </div>
            </div>
        );
    }

    if (!stats || stats.total_in_recovery === 0) {
        return (
            <div className="glass-panel rounded-xl p-5">
                <div className="flex items-center gap-3 mb-4">
                    <Activity className="w-5 h-5 text-green-400" />
                    <h3 className="text-lg font-semibold text-white">Recovery Status</h3>
                </div>
                <div className="text-center py-6 text-zinc-500">
                    <CheckCircle className="w-10 h-10 mx-auto mb-2 text-green-400 opacity-50" />
                    <p>All jobs are healthy</p>
                    <p className="text-xs text-zinc-600 mt-1">No jobs in recovery queue</p>
                </div>
            </div>
        );
    }

    return (
        <div className="glass-panel rounded-xl p-5">
            <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-3">
                    <Activity className="w-5 h-5 text-orange-400" />
                    <h3 className="text-lg font-semibold text-white">Recovery Status</h3>
                </div>
                <span className="text-xs px-2 py-1 bg-orange-500/10 text-orange-400 rounded-full">
                    {stats.total_in_recovery} jobs need attention
                </span>
            </div>

            {/* Stats Grid */}
            <div className="grid grid-cols-3 gap-3 mb-4">
                <div
                    onClick={() => setActiveTab("retries")}
                    className={`p-3 rounded-lg cursor-pointer transition-colors ${
                        activeTab === "retries"
                            ? "bg-orange-500/10 border border-orange-500/20"
                            : "bg-zinc-800/50 hover:bg-zinc-800"
                    }`}
                >
                    <div className="flex items-center gap-2">
                        <RefreshCw className="w-4 h-4 text-orange-400" />
                        <span className="text-2xl font-bold text-white">{stats.pending_retries}</span>
                    </div>
                    <p className="text-xs text-zinc-500 mt-1">Pending Retries</p>
                </div>
                <div
                    onClick={() => setActiveTab("review")}
                    className={`p-3 rounded-lg cursor-pointer transition-colors ${
                        activeTab === "review"
                            ? "bg-purple-500/10 border border-purple-500/20"
                            : "bg-zinc-800/50 hover:bg-zinc-800"
                    }`}
                >
                    <div className="flex items-center gap-2">
                        <UserCheck className="w-4 h-4 text-purple-400" />
                        <span className="text-2xl font-bold text-white">{stats.human_review_queue}</span>
                    </div>
                    <p className="text-xs text-zinc-500 mt-1">Human Review</p>
                </div>
                <div
                    onClick={() => setActiveTab("partial")}
                    className={`p-3 rounded-lg cursor-pointer transition-colors ${
                        activeTab === "partial"
                            ? "bg-amber-500/10 border border-amber-500/20"
                            : "bg-zinc-800/50 hover:bg-zinc-800"
                    }`}
                >
                    <div className="flex items-center gap-2">
                        <AlertTriangle className="w-4 h-4 text-amber-400" />
                        <span className="text-2xl font-bold text-white">{stats.partial_passes}</span>
                    </div>
                    <p className="text-xs text-zinc-500 mt-1">Partial Pass</p>
                </div>
            </div>

            {/* Tab Content */}
            <div className="max-h-64 overflow-y-auto">
                {activeTab === "retries" && (
                    <div className="space-y-2">
                        {retryJobs.length === 0 ? (
                            <p className="text-center text-zinc-500 py-4">No pending retries</p>
                        ) : (
                            retryJobs.map((job) => (
                                <div
                                    key={job.id}
                                    className="bg-zinc-800/50 rounded-lg p-3 flex items-center justify-between"
                                >
                                    <div className="flex items-center gap-3">
                                        <RefreshCw className="w-4 h-4 text-orange-400 animate-spin" />
                                        <div>
                                            <code className="text-xs text-zinc-400">
                                                #{job.id.slice(0, 8)}
                                            </code>
                                            <p className="text-sm text-white">
                                                Retry {job.retry_count}/{job.max_retries}
                                            </p>
                                        </div>
                                    </div>
                                    <div className="text-right">
                                        <div className="flex items-center gap-1 text-xs text-zinc-500">
                                            <Clock className="w-3 h-3" />
                                            {formatTimeRemaining(job.next_retry_at)}
                                        </div>
                                        {job.failure_reason && (
                                            <p className="text-xs text-red-400 truncate max-w-32">
                                                {job.failure_reason}
                                            </p>
                                        )}
                                    </div>
                                </div>
                            ))
                        )}
                    </div>
                )}

                {activeTab === "review" && (
                    <div className="space-y-2">
                        {humanReviewJobs.length === 0 ? (
                            <p className="text-center text-zinc-500 py-4">No jobs pending review</p>
                        ) : (
                            humanReviewJobs.map((job) => (
                                <div
                                    key={job.id}
                                    onClick={() => router.push(`/recovery/${job.id}`)}
                                    className="bg-zinc-800/50 rounded-lg p-3 flex items-center justify-between cursor-pointer hover:bg-zinc-800 transition-colors"
                                >
                                    <div className="flex items-center gap-3">
                                        <UserCheck className="w-4 h-4 text-purple-400" />
                                        <div>
                                            <code className="text-xs text-zinc-400">
                                                #{job.id.slice(0, 8)}
                                            </code>
                                            <p className="text-sm text-white">
                                                Exceeded {job.retry_count} retries
                                            </p>
                                        </div>
                                    </div>
                                    <div className="flex items-center gap-2">
                                        <span className={`text-xs px-2 py-0.5 rounded ${
                                            job.failure_severity === "critical"
                                                ? "bg-red-500/10 text-red-400"
                                                : "bg-yellow-500/10 text-yellow-400"
                                        }`}>
                                            {job.failure_severity || "unknown"}
                                        </span>
                                        <ChevronRight className="w-4 h-4 text-zinc-500" />
                                    </div>
                                </div>
                            ))
                        )}
                    </div>
                )}

                {activeTab === "partial" && (
                    <div className="text-center text-zinc-500 py-4">
                        <AlertTriangle className="w-8 h-8 mx-auto mb-2 text-amber-400 opacity-50" />
                        <p>Partial pass jobs can be viewed in job history</p>
                        <button
                            onClick={() => router.push("/jobs")}
                            className="mt-2 text-xs text-purple-400 hover:text-purple-300"
                        >
                            View all jobs →
                        </button>
                    </div>
                )}
            </div>
        </div>
    );
}
