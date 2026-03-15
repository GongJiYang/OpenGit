"use client";

import { useState, useMemo } from "react";
import {
  Layers,
  Clock,
  Play,
  CheckCircle,
  Pause,
  Loader2,
  User,
  ChevronDown,
  ChevronRight
} from "lucide-react";

interface Bounty {
  id: string;
  title: string;
  description: string;
  status: string;
  dependencies: string[];
  track?: string;
  assignee?: string;
  estimated_hours?: number;
  required_role: string;
  repo_name: string;
  parent_id?: string;
}

interface ParallelTracksViewProps {
  bounties: Bounty[];
  onTaskClick?: (bountyId: string) => void;
}

const STATUS_CONFIG: Record<string, { color: string; bgColor: string; icon: typeof Clock }> = {
  pending: { color: "text-zinc-400", bgColor: "bg-zinc-500/20", icon: Pause },
  ready_for_preparation: { color: "text-orange-400", bgColor: "bg-orange-500/20", icon: Clock },
  open: { color: "text-green-400", bgColor: "bg-green-500/20", icon: Clock },
  in_progress: { color: "text-blue-400", bgColor: "bg-blue-500/20", icon: Play },
  submitted: { color: "text-yellow-400", bgColor: "bg-yellow-500/20", icon: Loader2 },
  completed: { color: "text-emerald-400", bgColor: "bg-emerald-500/20", icon: CheckCircle },
};

const TRACK_COLORS: Record<string, { bg: string; border: string; text: string; accent: string }> = {
  backend: {
    bg: "bg-blue-500/5",
    border: "border-blue-500/30",
    text: "text-blue-400",
    accent: "bg-blue-500"
  },
  frontend: {
    bg: "bg-purple-500/5",
    border: "border-purple-500/30",
    text: "text-purple-400",
    accent: "bg-purple-500"
  },
  testing: {
    bg: "bg-green-500/5",
    border: "border-green-500/30",
    text: "text-green-400",
    accent: "bg-green-500"
  },
  infrastructure: {
    bg: "bg-orange-500/5",
    border: "border-orange-500/30",
    text: "text-orange-400",
    accent: "bg-orange-500"
  },
  default: {
    bg: "bg-zinc-500/5",
    border: "border-zinc-500/30",
    text: "text-zinc-400",
    accent: "bg-zinc-500"
  }
};

export default function ParallelTracksView({ bounties, onTaskClick }: ParallelTracksViewProps) {
  const [expandedTracks, setExpandedTracks] = useState<Set<string>>(new Set());

  // Group bounties by track
  const { trackGroups, untrackedTasks } = useMemo(() => {
    const groups: Record<string, Bounty[]> = {};
    const untracked: Bounty[] = [];

    bounties.forEach(bounty => {
      if (bounty.track) {
        if (!groups[bounty.track]) {
          groups[bounty.track] = [];
        }
        groups[bounty.track].push(bounty);
      } else {
        untracked.push(bounty);
      }
    });

    // Sort tasks within each track by status priority
    const statusPriority: Record<string, number> = {
      in_progress: 0,
      open: 1,
      ready_for_preparation: 2,
      pending: 3,
      submitted: 4,
      completed: 5
    };

    Object.keys(groups).forEach(track => {
      groups[track].sort((a, b) => {
        const priorityDiff = (statusPriority[a.status] || 99) - (statusPriority[b.status] || 99);
        if (priorityDiff !== 0) return priorityDiff;
        return (a.estimated_hours || 0) - (b.estimated_hours || 0);
      });
    });

    return { trackGroups: groups, untrackedTasks: untracked };
  }, [bounties]);

  const toggleTrack = (track: string) => {
    const newExpanded = new Set(expandedTracks);
    if (newExpanded.has(track)) {
      newExpanded.delete(track);
    } else {
      newExpanded.add(track);
    }
    setExpandedTracks(newExpanded);
  };

  // Calculate progress for a track
  const calculateProgress = (tasks: Bounty[]) => {
    const completed = tasks.filter(t => t.status === "completed").length;
    return {
      completed,
      total: tasks.length,
      percentage: tasks.length > 0 ? Math.round((completed / tasks.length) * 100) : 0
    };
  };

  const renderTask = (bounty: Bounty, trackConfig: typeof TRACK_COLORS.default) => {
    const config = STATUS_CONFIG[bounty.status] || STATUS_CONFIG.pending;
    const Icon = config.icon;

    return (
      <div
        key={bounty.id}
        onClick={() => onTaskClick?.(bounty.id)}
        className={`group p-3 rounded-lg border ${trackConfig.border} bg-black/30 hover:bg-black/50 cursor-pointer transition-all`}
      >
        <div className="flex items-start justify-between gap-2 mb-2">
          <h4 className="font-medium text-white text-sm line-clamp-1 group-hover:text-yellow-400 transition-colors">
            {bounty.title}
          </h4>
          <Icon className={`w-4 h-4 flex-shrink-0 ${config.color} ${bounty.status === 'in_progress' ? 'animate-pulse' : ''}`} />
        </div>

        <p className="text-xs text-zinc-500 line-clamp-2 mb-2">{bounty.description}</p>

        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className={`text-[10px] px-1.5 py-0.5 rounded ${config.bgColor} ${config.color} uppercase`}>
              {bounty.status.replace("_", " ")}
            </span>
            {bounty.estimated_hours && (
              <span className="text-[10px] text-zinc-500 flex items-center gap-0.5">
                <Clock className="w-2.5 h-2.5" />
                {bounty.estimated_hours}h
              </span>
            )}
          </div>

          {bounty.assignee && (
            <div className="flex items-center gap-1 text-[10px] text-blue-400">
              <User className="w-2.5 h-2.5" />
              {bounty.assignee.slice(0, 8)}
            </div>
          )}
        </div>

        {/* Dependencies indicator */}
        {bounty.dependencies.length > 0 && (
          <div className="mt-2 pt-2 border-t border-zinc-800">
            <div className="flex items-center gap-1 text-[10px] text-zinc-600">
              <Layers className="w-2.5 h-2.5" />
              {bounty.dependencies.length} dependenc{bounty.dependencies.length > 1 ? "ies" : "y"}
            </div>
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-bold text-white flex items-center gap-2">
          <Layers className="w-5 h-5 text-purple-400" />
          Parallel Tracks
        </h3>
        <span className="text-xs text-zinc-500">
          {Object.keys(trackGroups).length} tracks, {bounties.length} tasks
        </span>
      </div>

      {/* Track columns */}
      {Object.keys(trackGroups).length > 0 ? (
        <div className="grid gap-4">
          {Object.entries(trackGroups).map(([track, tasks]) => {
            const trackConfig = TRACK_COLORS[track] || TRACK_COLORS.default;
            const isExpanded = expandedTracks.has(track);
            const progress = calculateProgress(tasks);

            return (
              <div
                key={track}
                className={`border ${trackConfig.border} ${trackConfig.bg} rounded-xl overflow-hidden`}
              >
                {/* Track header */}
                <div
                  onClick={() => toggleTrack(track)}
                  className="flex items-center justify-between p-4 cursor-pointer hover:bg-white/5 transition-colors"
                >
                  <div className="flex items-center gap-3">
                    <div className={`w-3 h-3 rounded-full ${trackConfig.accent}`} />
                    <h4 className={`font-medium ${trackConfig.text} capitalize`}>{track}</h4>
                    <span className="text-xs text-zinc-500 bg-black/30 px-2 py-0.5 rounded-full">
                      {tasks.length} task{tasks.length > 1 ? "s" : ""}
                    </span>
                  </div>

                  <div className="flex items-center gap-3">
                    {/* Progress bar */}
                    <div className="flex items-center gap-2">
                      <div className="w-20 h-1.5 bg-zinc-800 rounded-full overflow-hidden">
                        <div
                          className={`h-full ${trackConfig.accent} transition-all duration-300`}
                          style={{ width: `${progress.percentage}%` }}
                        />
                      </div>
                      <span className="text-[10px] text-zinc-500">
                        {progress.completed}/{progress.total}
                      </span>
                    </div>

                    {isExpanded ? (
                      <ChevronDown className="w-4 h-4 text-zinc-400" />
                    ) : (
                      <ChevronRight className="w-4 h-4 text-zinc-400" />
                    )}
                  </div>
                </div>

                {/* Tasks in track */}
                {isExpanded && (
                  <div className="p-4 pt-0 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                    {tasks.map(bounty => renderTask(bounty, trackConfig))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      ) : (
        <div className="text-center py-8 border border-dashed border-zinc-800 rounded-xl">
          <Layers className="w-8 h-8 text-zinc-700 mx-auto mb-2" />
          <p className="text-zinc-500 text-sm">No tracks defined</p>
          <p className="text-zinc-600 text-xs mt-1">Use the 'track' field when creating bounties</p>
        </div>
      )}

      {/* Untracked tasks */}
      {untrackedTasks.length > 0 && (
        <div className="border border-zinc-700/50 bg-zinc-900/50 rounded-xl p-4">
          <div className="flex items-center justify-between mb-3">
            <h4 className="text-sm font-medium text-zinc-400 flex items-center gap-2">
              <Layers className="w-4 h-4" />
              Untracked Tasks
            </h4>
            <span className="text-xs text-zinc-600">{untrackedTasks.length}</span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {untrackedTasks.map(bounty => renderTask(bounty, TRACK_COLORS.default))}
          </div>
        </div>
      )}
    </div>
  );
}
