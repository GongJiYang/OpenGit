"use client";

import { useState, useEffect } from "react";
import {
  Lock,
  Unlock,
  AlertTriangle,
  GitBranch,
  Users,
  FileText,
  Clock,
  CheckCircle,
  XCircle,
  MessageSquare,
  RefreshCw,
  Loader2,
  Zap,
  Eye,
  Edit3,
  GitMerge
} from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "/api";

interface FileLock {
  file_path: string;
  agent_id: string;
  locked_at: string;
  expires_at: string;
}

interface ChangeRegion {
  file_path: string;
  start_line: number;
  end_line: number;
  agent_id: string;
  description: string;
  created_at: string;
}

interface Conflict {
  file_path: string;
  conflict_type: string;
  agents: string[];
  regions: Array<{ start: number; end: number }>;
  detected_at: string;
}

interface GlobalStatus {
  active_locks: number;
  files_with_changes: number;
  pending_reviews: number;
  active_conflicts: number;
  conflicts: Conflict[];
}

interface CodeReview {
  review_id: string;
  file_path: string;
  agent_id: string;
  reviewer_id: string | null;
  status: string;
  comments: Array<{
    author_id: string;
    content: string;
    line_number: number | null;
    created_at: string;
  }>;
  created_at: string;
  updated_at: string;
}

export default function CollaborationPanel() {
  const [globalStatus, setGlobalStatus] = useState<GlobalStatus | null>(null);
  const [fileLocks, setFileLocks] = useState<Record<string, FileLock>>({});
  const [changeRegions, setChangeRegions] = useState<Record<string, ChangeRegion[]>>({});
  const [reviews, setReviews] = useState<CodeReview[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [filePathInput, setFilePathInput] = useState("");

  const fetchGlobalStatus = async () => {
    setLoading(true);
    setError(null);

    try {
      const res = await fetch(`${API_BASE}/v1/collaboration/status/global`);
      if (!res.ok) throw new Error(`Failed to fetch: ${res.status}`);
      const data = await res.json();
      setGlobalStatus(data);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load status");
    } finally {
      setLoading(false);
    }
  };

  const fetchFileStatus = async (filePath: string) => {
    try {
      const res = await fetch(
        `${API_BASE}/v1/collaboration/status/file/${encodeURIComponent(filePath)}`
      );
      if (!res.ok) return;
      const data = await res.json();

      if (data.lock_info) {
        setFileLocks(prev => ({ ...prev, [filePath]: data.lock_info }));
      }
      if (data.change_regions) {
        setChangeRegions(prev => ({ ...prev, [filePath]: data.change_regions }));
      }
    } catch (e) {
      console.error("Failed to fetch file status:", e);
    }
  };

  useEffect(() => {
    fetchGlobalStatus();
    const interval = setInterval(fetchGlobalStatus, 15000);
    return () => clearInterval(interval);
  }, []);

  const getStatusColor = (status: string) => {
    switch (status) {
      case "approved": return "text-emerald-400 bg-emerald-500/10";
      case "rejected": return "text-red-400 bg-red-500/10";
      case "changes_requested": return "text-yellow-400 bg-yellow-500/10";
      default: return "text-zinc-400 bg-zinc-800";
    }
  };

  const getConflictSeverity = (conflictCount: number) => {
    if (conflictCount === 0) return { color: "text-emerald-400", bg: "bg-emerald-500/10" };
    if (conflictCount < 3) return { color: "text-yellow-400", bg: "bg-yellow-500/10" };
    return { color: "text-red-400", bg: "bg-red-500/10" };
  };

  if (loading && !globalStatus) {
    return (
      <div className="glass-panel rounded-xl p-6">
        <div className="flex items-center justify-center py-8">
          <Loader2 className="w-6 h-6 animate-spin text-zinc-500" />
        </div>
      </div>
    );
  }

  const severity = globalStatus ? getConflictSeverity(globalStatus.active_conflicts) : { color: "text-zinc-400", bg: "bg-zinc-800" };

  return (
    <div className="glass-panel rounded-xl p-6 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-bold text-white flex items-center gap-2">
          <GitMerge className="w-5 h-5 text-purple-400" />
          Collaboration Status
        </h3>
        <button
          onClick={fetchGlobalStatus}
          disabled={loading}
          className="flex items-center gap-1.5 px-2.5 py-1 text-xs rounded bg-zinc-800 text-zinc-300 hover:text-white transition-colors"
        >
          <RefreshCw className={`w-3 h-3 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </div>

      {/* Error Display */}
      {error && (
        <div className="p-3 bg-red-500/10 border border-red-500/20 rounded-lg">
          <div className="flex items-center gap-2 text-red-400 text-xs">
            <AlertTriangle className="w-3 h-3" />
            {error}
          </div>
        </div>
      )}

      {/* Stats Overview */}
      {globalStatus && (
        <div className="grid grid-cols-4 gap-3">
          <div className="bg-black/30 rounded-lg p-3 text-center">
            <div className="text-2xl font-bold text-blue-400">{globalStatus.active_locks}</div>
            <div className="text-xs text-zinc-500 flex items-center justify-center gap-1">
              <Lock className="w-3 h-3" /> Locks
            </div>
          </div>
          <div className="bg-black/30 rounded-lg p-3 text-center">
            <div className="text-2xl font-bold text-purple-400">{globalStatus.files_with_changes}</div>
            <div className="text-xs text-zinc-500 flex items-center justify-center gap-1">
              <Edit3 className="w-3 h-3" /> Editing
            </div>
          </div>
          <div className="bg-black/30 rounded-lg p-3 text-center">
            <div className="text-2xl font-bold text-yellow-400">{globalStatus.pending_reviews}</div>
            <div className="text-xs text-zinc-500 flex items-center justify-center gap-1">
              <Eye className="w-3 h-3" /> Reviews
            </div>
          </div>
          <div className={`bg-black/30 rounded-lg p-3 text-center ${severity.bg}`}>
            <div className={`text-2xl font-bold ${severity.color}`}>{globalStatus.active_conflicts}</div>
            <div className="text-xs text-zinc-500 flex items-center justify-center gap-1">
              <AlertTriangle className="w-3 h-3" /> Conflicts
            </div>
          </div>
        </div>
      )}

      {/* Active Conflicts */}
      {globalStatus && globalStatus.active_conflicts > 0 && (
        <div className="space-y-2">
          <h4 className="text-sm font-medium text-white flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-red-400" />
            Active Conflicts
          </h4>
          <div className="space-y-2 max-h-48 overflow-y-auto">
            {globalStatus.conflicts.map((conflict, i) => (
              <div
                key={i}
                className="bg-red-500/10 border border-red-500/20 rounded-lg p-3"
              >
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-mono text-white truncate flex-1">
                    {conflict.file_path}
                  </span>
                  <span className="text-[10px] text-red-400 bg-red-500/20 px-1.5 py-0.5 rounded ml-2">
                    {conflict.conflict_type}
                  </span>
                </div>
                <div className="flex items-center gap-2 text-[10px] text-zinc-400">
                  <Users className="w-3 h-3" />
                  <span>
                    Agents: {conflict.agents.slice(0, 2).map(a => a.slice(0, 8)).join(", ")}
                  </span>
                  <span className="text-zinc-600">|</span>
                  <span>
                    Lines: {conflict.regions.map(r => `${r.start}-${r.end}`).join(" vs ")}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* File Status Checker */}
      <div className="space-y-2">
        <h4 className="text-sm font-medium text-white flex items-center gap-2">
          <FileText className="w-4 h-4 text-blue-400" />
          Check File Status
        </h4>
        <div className="flex gap-2">
          <input
            type="text"
            value={filePathInput}
            onChange={e => setFilePathInput(e.target.value)}
            placeholder="Enter file path..."
            className="flex-1 bg-black/50 border border-zinc-800 rounded px-3 py-2 text-sm text-white focus:border-blue-500/50 focus:outline-none transition-colors"
          />
          <button
            onClick={() => {
              if (filePathInput.trim()) {
                fetchFileStatus(filePathInput.trim());
                setSelectedFile(filePathInput.trim());
              }
            }}
            className="px-3 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm rounded transition-colors"
          >
            Check
          </button>
        </div>

        {/* Selected File Status */}
        {selectedFile && (
          <div className="bg-black/30 rounded-lg p-3 border border-zinc-800">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-mono text-white truncate">{selectedFile}</span>
              <button
                onClick={() => {
                  setSelectedFile(null);
                  setFilePathInput("");
                }}
                className="text-zinc-500 hover:text-white text-xs"
              >
                Clear
              </button>
            </div>

            {/* Lock Status */}
            {fileLocks[selectedFile] ? (
              <div className="flex items-center gap-2 text-xs text-yellow-400 mb-2">
                <Lock className="w-3 h-3" />
                Locked by {fileLocks[selectedFile].agent_id.slice(0, 8)}
              </div>
            ) : (
              <div className="flex items-center gap-2 text-xs text-emerald-400 mb-2">
                <Unlock className="w-3 h-3" />
                Available for editing
              </div>
            )}

            {/* Change Regions */}
            {changeRegions[selectedFile] && changeRegions[selectedFile].length > 0 && (
              <div className="space-y-1">
                <div className="text-[10px] text-zinc-500">Active Changes:</div>
                {changeRegions[selectedFile].map((region, i) => (
                  <div key={i} className="text-xs text-zinc-400 flex items-center gap-2">
                    <Edit3 className="w-3 h-3 text-purple-400" />
                    Lines {region.start_line}-{region.end_line}
                    <span className="text-zinc-600">by</span>
                    <span className="text-purple-400">{region.agent_id.slice(0, 8)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Quick Actions */}
      <div className="pt-3 border-t border-zinc-800">
        <div className="flex items-center justify-between text-xs text-zinc-500">
          <span>Auto-refresh: 15s</span>
          <span className="flex items-center gap-1">
            <Zap className="w-3 h-3 text-yellow-400" />
            Conflict prevention enabled
          </span>
        </div>
      </div>
    </div>
  );
}
