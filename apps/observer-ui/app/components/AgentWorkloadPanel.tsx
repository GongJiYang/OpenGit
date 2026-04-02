"use client";

import { useState, useEffect } from "react";
import {
  Users,
  Activity,
  Clock,
  TrendingUp,
  AlertCircle,
  CheckCircle,
  Loader2,
  RefreshCw,
  Zap,
  Medal
} from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "/api";

interface AgentWorkload {
  agent_id: string;
  agent_name: string;
  role: string;
  status: string;
  current_active_tasks: number;
  max_concurrent_tasks: number;
  availability: number;
  active_task_ids: string[];
  completion_rate_7d: number | null;
  avg_completion_time_hours_7d: number | null;
}

interface WorkloadResponse {
  agents: AgentWorkload[];
  total_agents: number;
  available_agents: number;
  avg_availability: number;
}

const ROLE_COLORS: Record<string, string> = {
  architect: "text-purple-400 bg-purple-500/10",
  contributor: "text-blue-400 bg-blue-500/10",
  executor: "text-green-400 bg-green-500/10",
  tester: "text-orange-400 bg-orange-500/10",
};

const TIER_ICONS: Record<string, { icon: typeof Medal; color: string }> = {
  platinum: { icon: Medal, color: "text-cyan-400" },
  gold: { icon: Medal, color: "text-yellow-400" },
  silver: { icon: Medal, color: "text-zinc-300" },
  bronze: { icon: Medal, color: "text-orange-600" },
  new: { icon: Medal, color: "text-zinc-500" },
};

export default function AgentWorkloadPanel() {
  const [workload, setWorkload] = useState<WorkloadResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchWorkload = async () => {
    setLoading(true);
    setError(null);

    try {
      const res = await fetch(`${API_BASE}/v1/assignment/agents/workload`);
      if (!res.ok) {
        throw new Error(`Failed to fetch workload: ${res.status}`);
      }
      const data = await res.json();
      setWorkload(data);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load workload");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchWorkload();
    // Auto-refresh every 30 seconds
    const interval = setInterval(fetchWorkload, 30000);
    return () => clearInterval(interval);
  }, []);

  const getAvailabilityColor = (availability: number) => {
    if (availability >= 0.7) return "text-emerald-400";
    if (availability >= 0.3) return "text-yellow-400";
    return "text-red-400";
  };

  const getAvailabilityBg = (availability: number) => {
    if (availability >= 0.7) return "bg-emerald-500";
    if (availability >= 0.3) return "bg-yellow-500";
    return "bg-red-500";
  };

  if (loading && !workload) {
    return (
      <div className="glass-panel rounded-xl p-6">
        <div className="flex items-center justify-center py-8">
          <Loader2 className="w-6 h-6 animate-spin text-zinc-500" />
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="glass-panel rounded-xl p-6 border border-red-500/30">
        <div className="flex items-center gap-2 text-red-400">
          <AlertCircle className="w-4 h-4" />
          <span className="text-sm">{error}</span>
        </div>
      </div>
    );
  }

  if (!workload) return null;

  return (
    <div className="glass-panel rounded-xl p-6 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-bold text-white flex items-center gap-2">
          <Users className="w-5 h-5 text-blue-400" />
          Agent Workload
        </h3>
        <button
          onClick={fetchWorkload}
          disabled={loading}
          className="flex items-center gap-1.5 px-2.5 py-1 text-xs rounded bg-zinc-800 text-zinc-300 hover:text-white transition-colors"
        >
          <RefreshCw className={`w-3 h-3 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </div>

      {/* Stats Overview */}
      <div className="grid grid-cols-3 gap-3">
        <div className="bg-black/30 rounded-lg p-3">
          <div className="text-2xl font-bold text-white">{workload.total_agents}</div>
          <div className="text-xs text-zinc-500">Total Agents</div>
        </div>
        <div className="bg-black/30 rounded-lg p-3">
          <div className="text-2xl font-bold text-emerald-400">{workload.available_agents}</div>
          <div className="text-xs text-zinc-500">Available</div>
        </div>
        <div className="bg-black/30 rounded-lg p-3">
          <div className="text-2xl font-bold text-blue-400">
            {Math.round(workload.avg_availability * 100)}%
          </div>
          <div className="text-xs text-zinc-500">Avg Availability</div>
        </div>
      </div>

      {/* Agent List */}
      <div className="space-y-2">
        {workload.agents.length === 0 ? (
          <div className="text-center py-6 text-zinc-500 text-sm">
            No agents registered
          </div>
        ) : (
          workload.agents.map((agent) => (
            <div
              key={agent.agent_id}
              className="bg-black/20 rounded-lg p-3 border border-zinc-800 hover:border-zinc-700 transition-colors"
            >
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2">
                  <span className="font-medium text-white text-sm">
                    {agent.agent_name}
                  </span>
                  <span className={`text-[10px] px-1.5 py-0.5 rounded ${ROLE_COLORS[agent.role] || "text-zinc-400 bg-zinc-800"}`}>
                    {agent.role}
                  </span>
                  {agent.status === "claimed" && (
                    <span className="flex items-center gap-0.5 text-[10px] text-emerald-400">
                      <CheckCircle className="w-2.5 h-2.5" />
                      Active
                    </span>
                  )}
                </div>
                <span className={`text-xs font-mono ${getAvailabilityColor(agent.availability)}`}>
                  {Math.round(agent.availability * 100)}%
                </span>
              </div>

              {/* Workload Bar */}
              <div className="flex items-center gap-2">
                <div className="flex-1 h-1.5 bg-zinc-800 rounded-full overflow-hidden">
                  <div
                    className={`h-full ${getAvailabilityBg(agent.availability)} transition-all duration-300`}
                    style={{ width: `${agent.availability * 100}%` }}
                  />
                </div>
                <span className="text-[10px] text-zinc-500">
                  {agent.current_active_tasks}/{agent.max_concurrent_tasks} tasks
                </span>
              </div>

              {/* Performance Stats */}
              {agent.completion_rate_7d !== null && (
                <div className="flex items-center gap-3 mt-2 text-[10px] text-zinc-500">
                  <span className="flex items-center gap-0.5">
                    <TrendingUp className="w-2.5 h-2.5" />
                    {Math.round(agent.completion_rate_7d * 100)}% completion
                  </span>
                  {agent.avg_completion_time_hours_7d && (
                    <span className="flex items-center gap-0.5">
                      <Clock className="w-2.5 h-2.5" />
                      {agent.avg_completion_time_hours_7d.toFixed(1)}h avg
                    </span>
                  )}
                </div>
              )}
            </div>
          ))
        )}
      </div>

      {/* Quick Actions */}
      <div className="pt-3 border-t border-zinc-800">
        <div className="flex items-center justify-between text-xs text-zinc-500">
          <span>Auto-refresh: 30s</span>
          <span className="flex items-center gap-1">
            <Zap className="w-3 h-3 text-yellow-400" />
            Smart assignment enabled
          </span>
        </div>
      </div>
    </div>
  );
}
