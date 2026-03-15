"use client";

import { useMemo } from "react";
import {
  Circle,
  ArrowRight,
  CheckCircle,
  Clock,
  Play,
  Pause,
  Loader2,
  AlertCircle
} from "lucide-react";

interface BountyNode {
  id: string;
  title: string;
  status: string;
  dependencies: string[];
  track?: string;
  assignee?: string;
}

interface DAGVisualizationProps {
  bounties: BountyNode[];
  onNodeClick?: (bountyId: string) => void;
}

const STATUS_CONFIG: Record<string, { color: string; bgColor: string; icon: typeof Circle; label: string }> = {
  pending: { color: "text-zinc-400", bgColor: "bg-zinc-500/20", icon: Pause, label: "Pending" },
  ready_for_preparation: { color: "text-orange-400", bgColor: "bg-orange-500/20", icon: Clock, label: "Preparable" },
  open: { color: "text-green-400", bgColor: "bg-green-500/20", icon: Circle, label: "Open" },
  in_progress: { color: "text-blue-400", bgColor: "bg-blue-500/20", icon: Play, label: "In Progress" },
  submitted: { color: "text-yellow-400", bgColor: "bg-yellow-500/20", icon: Loader2, label: "Submitted" },
  completed: { color: "text-emerald-400", bgColor: "bg-emerald-500/20", icon: CheckCircle, label: "Completed" },
  cancelled: { color: "text-red-400", bgColor: "bg-red-500/20", icon: AlertCircle, label: "Cancelled" },
};

const TRACK_COLORS: Record<string, string> = {
  backend: "border-blue-500/50",
  frontend: "border-purple-500/50",
  testing: "border-green-500/50",
  default: "border-zinc-500/50",
};

export default function DAGVisualization({ bounties, onNodeClick }: DAGVisualizationProps) {
  // Build adjacency list and detect levels
  const { nodesByLevel, edges } = useMemo(() => {
    const bountyMap = new Map(bounties.map(b => [b.id, b]));
    const visited = new Set<string>();
    const levels = new Map<string, number>();
    const edgesList: { from: string; to: string }[] = [];

    // Calculate levels using BFS from nodes with no dependencies
    function calculateLevel(nodeId: string): number {
      if (levels.has(nodeId)) return levels.get(nodeId)!;

      const node = bountyMap.get(nodeId);
      if (!node || node.dependencies.length === 0) {
        levels.set(nodeId, 0);
        return 0;
      }

      const maxDepLevel = Math.max(
        ...node.dependencies.map(depId => {
          if (!bountyMap.has(depId)) return 0;
          return calculateLevel(depId);
        })
      );

      const level = maxDepLevel + 1;
      levels.set(nodeId, level);
      return level;
    }

    // Calculate all levels
    bounties.forEach(b => calculateLevel(b.id));

    // Build edges
    bounties.forEach(b => {
      b.dependencies.forEach(depId => {
        if (bountyMap.has(depId)) {
          edgesList.push({ from: depId, to: b.id });
        }
      });
    });

    // Group by level
    const maxLevel = Math.max(...Array.from(levels.values()), 0);
    const byLevel: BountyNode[][] = Array.from({ length: maxLevel + 1 }, () => []);

    bounties.forEach(b => {
      const level = levels.get(b.id) || 0;
      byLevel[level].push(b);
    });

    // Sort within each level by track
    byLevel.forEach(level => {
      level.sort((a, b) => {
        const trackA = a.track || "default";
        const trackB = b.track || "default";
        return trackA.localeCompare(trackB);
      });
    });

    return { nodesByLevel: byLevel, edges: edgesList };
  }, [bounties]);

  // Get position for edge drawing
  const getNodePosition = (nodeId: string, levelIdx: number, nodeIdx: number, levelSize: number) => {
    const x = 150 + levelIdx * 280;
    const y = 80 + nodeIdx * 100;
    return { x, y };
  };

  if (bounties.length === 0) {
    return (
      <div className="text-center py-12 border border-dashed border-zinc-800 rounded-xl">
        <Circle className="w-10 h-10 text-zinc-700 mx-auto mb-3" />
        <p className="text-zinc-500 text-sm">No tasks to visualize</p>
        <p className="text-xs text-zinc-600 mt-1">Create hierarchical bounties to see the DAG</p>
      </div>
    );
  }

  return (
    <div className="relative overflow-x-auto">
      {/* SVG for edges */}
      <svg className="absolute inset-0 pointer-events-none" style={{ minWidth: nodesByLevel.length * 280 + 100 }}>
        {edges.map((edge, idx) => {
          const fromLevel = nodesByLevel.findIndex(level => level.some(n => n.id === edge.from));
          const toLevel = nodesByLevel.findIndex(level => level.some(n => n.id === edge.to));
          const fromIdx = nodesByLevel[fromLevel]?.findIndex(n => n.id === edge.from) || 0;
          const toIdx = nodesByLevel[toLevel]?.findIndex(n => n.id === edge.to) || 0;

          const from = getNodePosition(edge.from, fromLevel, fromIdx, nodesByLevel[fromLevel]?.length || 1);
          const to = getNodePosition(edge.to, toLevel, toIdx, nodesByLevel[toLevel]?.length || 1);

          const fromBounty = bounties.find(b => b.id === edge.from);
          const toBounty = bounties.find(b => b.id === edge.to);
          const isCompleted = fromBounty?.status === "completed";
          const isBlocked = fromBounty?.status !== "completed";

          return (
            <g key={idx}>
              <line
                x1={from.x + 120}
                y1={from.y + 30}
                x2={to.x - 10}
                y2={to.y + 30}
                stroke={isCompleted ? "#22c55e" : isBlocked ? "#52525b" : "#71717a"}
                strokeWidth={isCompleted ? 2 : 1}
                strokeDasharray={isBlocked ? "4 4" : "none"}
                className="transition-all"
              />
              <polygon
                points={`${to.x - 10},${to.y + 25} ${to.x - 10},${to.y + 35} ${to.x},${to.y + 30}`}
                fill={isCompleted ? "#22c55e" : "#52525b"}
              />
            </g>
          );
        })}
      </svg>

      {/* Node columns */}
      <div className="flex gap-8 relative z-10 p-4" style={{ minWidth: nodesByLevel.length * 280 + 100 }}>
        {nodesByLevel.map((level, levelIdx) => (
          <div key={levelIdx} className="flex flex-col gap-4">
            {/* Level header */}
            <div className="text-center mb-2">
              <span className="text-xs text-zinc-500 uppercase tracking-wider">
                {levelIdx === 0 ? "Start" : `Level ${levelIdx}`}
              </span>
            </div>

            {/* Nodes in this level */}
            {level.map((bounty) => {
              const config = STATUS_CONFIG[bounty.status] || STATUS_CONFIG.pending;
              const Icon = config.icon;
              const trackBorder = bounty.track ? TRACK_COLORS[bounty.track] : TRACK_COLORS.default;

              return (
                <div
                  key={bounty.id}
                  onClick={() => onNodeClick?.(bounty.id)}
                  className={`
                    relative w-60 p-4 rounded-xl border cursor-pointer
                    transition-all hover:scale-[1.02]
                    ${config.bgColor} ${trackBorder}
                    hover:shadow-lg hover:shadow-black/20
                  `}
                >
                  {/* Track badge */}
                  {bounty.track && (
                    <span className="absolute -top-2 -right-2 text-[10px] px-2 py-0.5 bg-zinc-800 text-zinc-400 rounded-full uppercase tracking-wider">
                      {bounty.track}
                    </span>
                  )}

                  <div className="flex items-start gap-3">
                    <div className={`mt-0.5 ${config.color}`}>
                      <Icon className={`w-4 h-4 ${bounty.status === "submitted" ? "animate-spin" : ""}`} />
                    </div>
                    <div className="flex-1 min-w-0">
                      <h4 className="text-sm font-medium text-zinc-200 truncate">{bounty.title}</h4>
                      <div className="flex items-center gap-2 mt-1">
                        <span className={`text-[10px] px-1.5 py-0.5 rounded ${config.bgColor} ${config.color}`}>
                          {config.label}
                        </span>
                        {bounty.assignee && (
                          <span className="text-[10px] text-blue-400 truncate">
                            by {bounty.assignee.slice(0, 8)}...
                          </span>
                        )}
                      </div>
                    </div>
                  </div>

                  {/* Dependency count */}
                  {bounty.dependencies.length > 0 && (
                    <div className="mt-2 pt-2 border-t border-white/5">
                      <span className="text-[10px] text-zinc-500">
                        Depends on {bounty.dependencies.length} task{bounty.dependencies.length > 1 ? "s" : ""}
                      </span>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        ))}
      </div>

      {/* Legend */}
      <div className="flex flex-wrap gap-4 mt-6 p-4 bg-black/30 rounded-xl border border-zinc-800">
        <span className="text-xs text-zinc-500 uppercase tracking-wider mr-2">Status:</span>
        {Object.entries(STATUS_CONFIG).map(([status, config]) => {
          const Icon = config.icon;
          return (
            <div key={status} className="flex items-center gap-1.5">
              <Icon className={`w-3 h-3 ${config.color}`} />
              <span className="text-xs text-zinc-400">{config.label}</span>
            </div>
          );
        })}
        <div className="border-l border-zinc-700 pl-4 ml-2 flex items-center gap-4">
          <div className="flex items-center gap-1.5">
            <div className="w-6 h-0.5 bg-emerald-500" />
            <span className="text-xs text-zinc-400">Completed</span>
          </div>
          <div className="flex items-center gap-1.5">
            <div className="w-6 h-0.5 bg-zinc-500 border-dashed" style={{ borderStyle: 'dashed' }} />
            <span className="text-xs text-zinc-400">Blocked</span>
          </div>
        </div>
      </div>
    </div>
  );
}
