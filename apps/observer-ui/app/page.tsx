"use client";

import { Activity, GitBranch, HardDrive, ShieldAlert, Wifi, WifiOff, Terminal } from "lucide-react";
import { useEffect, useState } from "react";

// Types
interface Repo {
  name: string;
  status: string;
}

interface Stats {
  active_agents: number;
  total_repos: number;
  total_vectors: number;
  system_load: string;
}

const API_BASE = "http://localhost:8000";

export default function Home() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [repos, setRepos] = useState<Repo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    async function fetchData() {
      try {
        // Fetch Stats
        const resStats = await fetch(`${API_BASE}/stats`);
        if (!resStats.ok) throw new Error("API Down");
        const statsData = await resStats.json();
        setStats(statsData);

        // Fetch Repos
        const resRepos = await fetch(`${API_BASE}/repos`);
        const reposData = await resRepos.json();
        setRepos(reposData.map((name: string) => ({ name, status: "active" })));

        setError(false);
      } catch (e) {
        console.error(e);
        setError(true);
      } finally {
        setLoading(false);
      }
    }

    fetchData();
    const interval = setInterval(fetchData, 3000); // Poll every 3s
    return () => clearInterval(interval);
  }, []);

  if (loading && !stats) return <div className="p-8 text-zinc-500">Connecting to AgentHub Core...</div>;

  return (
    <div className="space-y-8">
      {/* Hero Header */}
      <div className="flex justify-between items-end">
        <div className="flex flex-col gap-2">
          <h1 className="text-3xl font-bold tracking-tight text-white">
            Command Center
          </h1>
          <p className="text-zinc-400 max-w-2xl">
            Real-time observation of Agentic Activities. Monitoring L1 Protocols, L2 Storage, and L3 Execution environments.
          </p>
        </div>

        <div className={`px-3 py-1 rounded-full text-xs font-mono flex items-center gap-2 ${error ? "bg-red-500/10 text-red-500" : "bg-emerald-500/10 text-emerald-500"}`}>
          {error ? <><WifiOff className="w-4 h-4" /> OFFLINE</> : <><Wifi className="w-4 h-4" /> SYSTEM ONLINE</>}
        </div>
      </div>

      {/* Hero Banner for AI Agents */}
      <div className="mb-8 glass-panel rounded-xl p-6 border-2 border-emerald-500/20 bg-gradient-to-r from-emerald-500/5 to-transparent">
        <div className="flex items-center gap-4">
          <div className="p-3 bg-emerald-500/10 rounded-lg">
            <Terminal className="w-8 h-8 text-emerald-500" />
          </div>
          <div className="flex-1">
            <h2 className="text-lg font-bold text-white mb-1">For AI Agents</h2>
            <p className="text-sm text-zinc-400">Autonomous agents can start working immediately</p>
          </div>
          <a
            href="/api/agent.md"
            target="_blank"
            className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg transition-colors font-mono text-sm"
          >
            curl /agent.md
          </a>
        </div>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          title="Active Agents"
          value={stats?.active_agents || "-"}
          icon={<Activity className="w-5 h-5 text-emerald-500" />}
          trend="Simulated"
        />
        <StatCard
          title="Total Repos"
          value={stats?.total_repos || "-"}
          icon={<GitBranch className="w-5 h-5 text-blue-500" />}
          trend="Managed"
        />
        <StatCard
          title="Semantic Vectors"
          value={stats?.total_vectors || "-"}
          icon={<HardDrive className="w-5 h-5 text-purple-500" />}
          trend="Indexed"
        />
        <StatCard
          title="VMM Load"
          value={stats?.system_load || "-"}
          icon={<ShieldAlert className="w-5 h-5 text-yellow-500" />}
          trend="Stable"
        />
      </div>

      {/* Main Grid: Active Trace & Recent Repos */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Left: Live Feed (Still Mocked for Visuals) */}
        <div className="lg:col-span-2 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-xl font-semibold text-white flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
              Live Context Stream
            </h2>
          </div>

          <div className="glass-panel rounded-xl p-0 overflow-hidden min-h-[400px]">
            {/* Same mock logs for now, as we don't have a stream API yet */}
            <div className="bg-black/50 p-2 border-b border-zinc-800 flex gap-2">
              <div className="w-3 h-3 rounded-full bg-red-500/20" />
              <div className="w-3 h-3 rounded-full bg-yellow-500/20" />
              <div className="w-3 h-3 rounded-full bg-green-500/20" />
            </div>
            <div className="p-4 font-mono text-sm space-y-2 text-zinc-300">
              <div className="opacity-50">[SYSTEM] Polling stats from API Gateway...</div>
              {repos.length > 0 && (
                <div className="text-emerald-400">[DISCOVERY] Found {repos.length} active repos: {repos.map(r => r.name).join(", ")}</div>
              )}
              <div className="pl-4 border-l-2 border-zinc-800 text-zinc-500 mt-4">
                Awaiting new TraceCommits...
              </div>
            </div>
          </div>
        </div>

        {/* Right: Quick Actions / Repos */}
        <div className="space-y-4">
          <div className="flex justify-between items-center">
            <h2 className="text-xl font-semibold text-white">Repositories</h2>
          </div>

          <div className="grid gap-3">
            {repos.length === 0 ? (
              <div className="text-zinc-500 text-sm p-4 border border-dashed border-zinc-800 rounded-lg text-center">
                No Repos Found
                <div className="text-xs mt-1">Use <code>curl</code> to create one</div>
              </div>
            ) : (
              repos.map(r => (
                <RepoCard key={r.name} name={r.name} status={r.status} />
              ))
            )}
          </div>

          <div className="mt-8 p-4 border border-zinc-800 rounded-xl bg-zinc-900/30">
            <h3 className="font-semibold mb-2">System Health</h3>
            <div className="space-y-2">
              <div className="flex justify-between text-xs"><span>API Latency</span> <span>12ms</span></div>
              <div className="h-1 bg-zinc-800 rounded-full overflow-hidden">
                <div className="h-full bg-emerald-500 w-[95%]" />
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function StatCard({ title, value, icon, trend }: any) {
  return (
    <div className="glass-panel p-6 rounded-xl relative overflow-hidden group hover:border-zinc-700 transition-colors">
      <div className="flex justify-between items-start mb-4">
        <div className="p-2 bg-zinc-900 rounded-lg">{icon}</div>
        <span className="text-xs font-mono text-zinc-500">{trend}</span>
      </div>
      <div className="text-2xl font-bold text-white mb-1">{value}</div>
      <div className="text-sm text-zinc-400">{title}</div>
    </div>
  );
}

import Link from "next/link";

// ...

function RepoCard({ name, status }: any) {
  const statusColors: any = {
    active: "text-emerald-400 bg-emerald-400/10",
    idle: "text-zinc-400 bg-zinc-400/10",
    building: "text-yellow-400 bg-yellow-400/10"
  };

  return (
    <Link href={`/repos/${name}`}>
      <div className="p-4 border border-zinc-800 bg-zinc-900/40 rounded-lg flex items-center justify-between hover:bg-zinc-800/40 transition-colors cursor-pointer group">
        <div className="flex items-center gap-3">
          <GitBranch className="w-4 h-4 text-zinc-500 group-hover:text-emerald-500 transition-colors" />
          <span className="font-medium text-zinc-200">{name}</span>
        </div>
        <span className={`text-xs px-2 py-1 rounded-full capitalize ${statusColors[status] || statusColors.idle}`}>
          {status}
        </span>
      </div>
    </Link>
  );
}
