"use client";

import { useState, useEffect } from "react";
import {
  Activity,
  RefreshCw,
  Search,
  Filter,
  Server,
  Clock,
  CheckCircle,
  XCircle,
  Loader2,
  Globe
} from "lucide-react";
import ServiceEndpointPanel from "../components/ServiceEndpointPanel";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "/api";

interface ComputeJob {
  id: string;
  bounty_id: string;
  repo_id: string | null;
  runner_id: string | null;
  status: string;
  execution_mode: string;
  test_command: string;
  exit_code: number | null;
  passed: boolean | null;
  is_audited: boolean;
  audit_result: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  service_endpoint?: string | null;
  access_token?: string | null;
}

type StatusFilter = "all" | "running" | "completed" | "failed" | "pending";

const STATUS_CONFIG: Record<string, { color: string; bgColor: string; icon: typeof Clock }> = {
  pending: { color: "text-zinc-400", bgColor: "bg-zinc-500/20", icon: Clock },
  assigned: { color: "text-blue-400", bgColor: "bg-blue-500/20", icon: Loader2 },
  running: { color: "text-blue-400", bgColor: "bg-blue-500/20", icon: Activity },
  completed: { color: "text-emerald-400", bgColor: "bg-emerald-500/20", icon: CheckCircle },
  failed: { color: "text-red-400", bgColor: "bg-red-500/20", icon: XCircle },
  timeout: { color: "text-orange-400", bgColor: "bg-orange-500/20", icon: Clock },
};

export default function TestingPage() {
  const [jobs, setJobs] = useState<ComputeJob[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<StatusFilter>("all");
  const [searchTerm, setSearchTerm] = useState("");
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);

  const fetchJobs = async () => {
    setLoading(true);
    setError(null);

    try {
      const res = await fetch(`${API_BASE}/v1/runners/jobs`);

      if (!res.ok) {
        throw new Error(`Failed to fetch jobs: ${res.status}`);
      }

      const data = await res.json();
      setJobs(data || []);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load jobs");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchJobs();

    // Auto-refresh every 30 seconds
    const interval = setInterval(fetchJobs, 30000);
    return () => clearInterval(interval);
  }, []);

  const filteredJobs = jobs.filter(job => {
    const matchesStatus = filter === "all" || job.status === filter;
    const matchesSearch = !searchTerm ||
      job.bounty_id.toLowerCase().includes(searchTerm.toLowerCase()) ||
      job.id.toLowerCase().includes(searchTerm.toLowerCase());
    return matchesStatus && matchesSearch;
  });

  const statusCounts = {
    all: jobs.length,
    pending: jobs.filter(j => j.status === "pending").length,
    running: jobs.filter(j => j.status === "running" || j.status === "assigned").length,
    completed: jobs.filter(j => j.status === "completed").length,
    failed: jobs.filter(j => j.status === "failed" || j.status === "timeout").length,
  };

  const getTimeAgo = (dateStr: string) => {
    const date = new Date(dateStr);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMins / 60);
    const diffDays = Math.floor(diffHours / 24);

    if (diffDays > 0) return `${diffDays}d ago`;
    if (diffHours > 0) return `${diffHours}h ago`;
    if (diffMins > 0) return `${diffMins}m ago`;
    return "just now";
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-3xl font-bold text-white flex items-center gap-3">
            <Server className="w-8 h-8 text-purple-500" />
            Service Testing
          </h1>
          <p className="text-zinc-400 mt-2 max-w-xl">
            Access deployed services for blackbox testing. Use the endpoint URL and access token to test running services.
          </p>
        </div>
        <button
          onClick={fetchJobs}
          disabled={loading}
          className="flex items-center gap-2 px-3 py-1.5 text-xs rounded bg-zinc-800 text-zinc-300 hover:text-white transition-colors"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </div>

      {/* Search and Filter */}
      <div className="flex items-center gap-4">
        <div className="relative flex-1 max-w-md">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-500" />
          <input
            type="text"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            placeholder="Search by bounty ID or job ID..."
            className="w-full bg-black/50 border border-zinc-800 rounded-lg pl-10 pr-4 py-2 text-sm text-white focus:border-purple-500/50 focus:outline-none transition-colors"
          />
        </div>

        <div className="flex items-center gap-1 bg-zinc-900 p-1 rounded-lg">
          {(["all", "running", "pending", "completed", "failed"] as StatusFilter[]).map(status => (
            <button
              key={status}
              onClick={() => setFilter(status)}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium transition-colors ${
                filter === status
                  ? "bg-purple-500/20 text-purple-400 border border-purple-500/30"
                  : "text-zinc-400 hover:text-white"
              }`}
            >
              {status === "running" && <Activity className="w-3 h-3" />}
              {status === "pending" && <Clock className="w-3 h-3" />}
              {status === "completed" && <CheckCircle className="w-3 h-3" />}
              {status === "failed" && <XCircle className="w-3 h-3" />}
              {status === "all" && <Filter className="w-3 h-3" />}
              <span className="capitalize">{status}</span>
              <span className="opacity-60">({statusCounts[status]})</span>
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="p-3 border border-red-500/30 bg-red-500/10 text-red-300 text-xs rounded">
          {error}
        </div>
      )}

      {/* Main Content */}
      <div className="flex gap-6">
        {/* Job List */}
        <div className="flex-1 space-y-3">
          {loading && jobs.length === 0 ? (
            <div className="flex items-center justify-center py-16">
              <Loader2 className="w-6 h-6 animate-spin text-zinc-500" />
            </div>
          ) : filteredJobs.length === 0 ? (
            <div className="text-center py-16 border border-dashed border-zinc-800 rounded-xl">
              <Server className="w-12 h-12 text-zinc-700 mx-auto mb-4" />
              <p className="text-zinc-500">No jobs found</p>
              <p className="text-xs text-zinc-600 mt-1">
                {filter !== "all" ? `No ${filter} jobs` : "No compute jobs available"}
              </p>
            </div>
          ) : (
            filteredJobs.map((job) => {
              const config = STATUS_CONFIG[job.status] || STATUS_CONFIG.pending;
              const Icon = config.icon;
              const isSelected = selectedJobId === job.id;

              return (
                <div
                  key={job.id}
                  onClick={() => setSelectedJobId(isSelected ? null : job.id)}
                  className={`glass-panel p-4 rounded-xl cursor-pointer transition-all ${
                    isSelected
                      ? "border-purple-500/50 bg-purple-500/5"
                      : "hover:border-zinc-700"
                  }`}
                >
                  <div className="flex items-start justify-between gap-4">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <span className={`flex items-center gap-1 text-xs px-2 py-0.5 rounded-full ${config.bgColor} ${config.color}`}>
                          <Icon className={`w-3 h-3 ${job.status === "running" ? "animate-pulse" : ""}`} />
                          {job.status}
                        </span>
                        {job.service_endpoint && (
                          <span className="flex items-center gap-1 text-xs text-emerald-400">
                            <Globe className="w-3 h-3" />
                            Endpoint ready
                          </span>
                        )}
                      </div>

                      <div className="text-sm text-zinc-300 font-mono truncate">
                        Job: {job.id.slice(0, 8)}...
                      </div>
                      <div className="text-xs text-zinc-500 mt-1">
                        Bounty: {job.bounty_id.slice(0, 12)}...
                      </div>

                      <div className="flex items-center gap-3 mt-2 text-xs text-zinc-500">
                        <span>Created {getTimeAgo(job.created_at)}</span>
                        {job.completed_at && (
                          <span>• Completed {getTimeAgo(job.completed_at)}</span>
                        )}
                      </div>

                      {job.passed !== null && (
                        <div className={`flex items-center gap-1 text-xs mt-2 ${job.passed ? "text-emerald-400" : "text-red-400"}`}>
                          {job.passed ? <CheckCircle className="w-3 h-3" /> : <XCircle className="w-3 h-3" />}
                          Tests {job.passed ? "passed" : "failed"}
                          {job.exit_code !== null && ` (exit: ${job.exit_code})`}
                        </div>
                      )}
                    </div>

                    <div className="text-right text-xs text-zinc-500">
                      <div className="text-zinc-400">{job.execution_mode}</div>
                      {job.runner_id && (
                        <div className="mt-1">Runner: {job.runner_id.slice(0, 8)}...</div>
                      )}
                    </div>
                  </div>
                </div>
              );
            })
          )}
        </div>

        {/* Service Endpoint Panel */}
        <div className="w-96 sticky top-24">
          {selectedJobId ? (
            <ServiceEndpointPanel jobId={selectedJobId} />
          ) : (
            <div className="glass-panel rounded-xl p-6 text-center">
              <Server className="w-10 h-10 text-zinc-700 mx-auto mb-3" />
              <p className="text-zinc-500 text-sm">Select a job to view</p>
              <p className="text-zinc-600 text-xs mt-1">
                Click on any job to see its service endpoint and access token
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
