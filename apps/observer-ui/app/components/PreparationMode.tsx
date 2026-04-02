"use client";

import { useState } from "react";
import {
  Clock,
  Play,
  CheckCircle,
  AlertTriangle,
  Loader2,
  Zap,
  Eye,
  FileText,
  ChevronDown,
  ChevronUp
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
}

interface PreparationModeProps {
  bounties: Bounty[];
  onClaimPreparation: (bountyId: string, notes: string) => Promise<void>;
  onActivate: (bountyId: string) => Promise<void>;
  onViewDetails?: (bountyId: string) => void;
}

const STATUS_CONFIG: Record<string, { color: string; bgColor: string; borderColor: string }> = {
  pending: { color: "text-zinc-400", bgColor: "bg-zinc-500/10", borderColor: "border-zinc-500/30" },
  ready_for_preparation: { color: "text-orange-400", bgColor: "bg-orange-500/10", borderColor: "border-orange-500/30" },
  open: { color: "text-green-400", bgColor: "bg-green-500/10", borderColor: "border-green-500/30" },
  in_progress: { color: "text-blue-400", bgColor: "bg-blue-500/10", borderColor: "border-blue-500/30" },
};

export default function PreparationMode({ bounties, onClaimPreparation, onActivate, onViewDetails }: PreparationModeProps) {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [preparationNotes, setPreparationNotes] = useState<Record<string, string>>({});
  const [isSubmitting, setIsSubmitting] = useState<string | null>(null);

  // Filter for preparable bounties
  const preparableBounties = bounties.filter(b =>
    b.status === "ready_for_preparation" || b.status === "pending"
  );

  // Check if dependencies are completed
  const areDependenciesCompleted = (bounty: Bounty): boolean => {
    return bounty.dependencies.every(depId => {
      const depBounty = bounties.find(b => b.id === depId);
      return depBounty?.status === "completed";
    });
  };

  const handleClaimPreparation = async (bountyId: string) => {
    const notes = preparationNotes[bountyId] || "";
    setIsSubmitting(bountyId);
    try {
      await onClaimPreparation(bountyId, notes);
      setPreparationNotes(prev => {
        const next = { ...prev };
        delete next[bountyId];
        return next;
      });
    } finally {
      setIsSubmitting(null);
    }
  };

  const handleActivate = async (bountyId: string) => {
    setIsSubmitting(bountyId);
    try {
      await onActivate(bountyId);
    } finally {
      setIsSubmitting(null);
    }
  };

  if (preparableBounties.length === 0) {
    return (
      <div className="glass-panel rounded-xl p-6">
        <div className="flex items-center gap-2 mb-4">
          <Zap className="w-5 h-5 text-orange-400" />
          <h3 className="text-lg font-bold text-white">Preparation Mode</h3>
        </div>
        <div className="text-center py-8 text-zinc-500">
          <Clock className="w-8 h-8 mx-auto mb-2 opacity-50" />
          <p className="text-sm">No tasks available for preparation</p>
          <p className="text-xs mt-1">Tasks with dependencies will appear here</p>
        </div>
      </div>
    );
  }

  return (
    <div className="glass-panel rounded-xl p-6">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Zap className="w-5 h-5 text-orange-400" />
          <h3 className="text-lg font-bold text-white">Preparation Mode</h3>
          <span className="text-xs bg-orange-500/20 text-orange-400 px-2 py-0.5 rounded-full">
            {preparableBounties.length} available
          </span>
        </div>
        <p className="text-xs text-zinc-500">Claim early, prepare while dependencies complete</p>
      </div>

      <div className="space-y-3">
        {preparableBounties.map(bounty => {
          const isExpanded = expandedId === bounty.id;
          const config = STATUS_CONFIG[bounty.status] || STATUS_CONFIG.pending;
          const depsCompleted = areDependenciesCompleted(bounty);
          const hasAssignee = Boolean(bounty.assignee);

          return (
            <div
              key={bounty.id}
              className={`border ${config.borderColor} ${config.bgColor} rounded-lg overflow-hidden transition-all`}
            >
              {/* Header */}
              <div
                onClick={() => setExpandedId(isExpanded ? null : bounty.id)}
                className="p-4 cursor-pointer hover:bg-white/5 transition-colors"
              >
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-1">
                      <h4 className="font-medium text-white">{bounty.title}</h4>
                      <span className={`text-[10px] px-1.5 py-0.5 rounded uppercase ${config.color} border ${config.borderColor}`}>
                        {bounty.status.replace("_", " ")}
                      </span>
                      {bounty.track && (
                        <span className="text-[10px] bg-blue-500/20 text-blue-400 px-1.5 py-0.5 rounded">
                          {bounty.track}
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-zinc-400 line-clamp-1">{bounty.description}</p>
                  </div>

                  <div className="flex items-center gap-2 ml-4">
                    {bounty.estimated_hours && (
                      <span className="text-xs text-zinc-500 flex items-center gap-1">
                        <Clock className="w-3 h-3" />
                        {bounty.estimated_hours}h
                      </span>
                    )}
                    {isExpanded ? (
                      <ChevronUp className="w-4 h-4 text-zinc-400" />
                    ) : (
                      <ChevronDown className="w-4 h-4 text-zinc-400" />
                    )}
                  </div>
                </div>

                {/* Status indicators */}
                <div className="flex items-center gap-3 mt-2">
                  {/* Dependencies status */}
                  {bounty.dependencies.length > 0 && (
                    <div className={`flex items-center gap-1 text-[10px] ${depsCompleted ? "text-green-400" : "text-orange-400"}`}>
                      {depsCompleted ? (
                        <CheckCircle className="w-3 h-3" />
                      ) : (
                        <Clock className="w-3 h-3" />
                      )}
                      {depsCompleted ? "Deps ready" : `${bounty.dependencies.length} deps pending`}
                    </div>
                  )}

                  {/* Claim status */}
                  {hasAssignee && (
                    <div className="flex items-center gap-1 text-[10px] text-zinc-500">
                      <AlertTriangle className="w-3 h-3" />
                      Claimed
                    </div>
                  )}
                </div>
              </div>

              {/* Expanded content */}
              {isExpanded && (
                <div className="border-t border-zinc-700/50 p-4 bg-black/30">
                  {/* Dependencies detail */}
                  {bounty.dependencies.length > 0 && (
                    <div className="mb-4">
                      <h5 className="text-xs text-zinc-500 uppercase mb-2">Dependencies</h5>
                      <div className="space-y-1">
                        {bounty.dependencies.map(depId => {
                          const depBounty = bounties.find(b => b.id === depId);
                          const isCompleted = depBounty?.status === "completed";
                          return (
                            <div key={depId} className="flex items-center gap-2 text-xs">
                              {isCompleted ? (
                                <CheckCircle className="w-3 h-3 text-green-400" />
                              ) : (
                                <Clock className="w-3 h-3 text-orange-400" />
                              )}
                              <span className={isCompleted ? "text-zinc-300" : "text-zinc-500"}>
                                {depBounty?.title || depId}
                              </span>
                              <span className={`text-[10px] ${isCompleted ? "text-green-400" : "text-orange-400"}`}>
                                ({depBounty?.status || "unknown"})
                              </span>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}

                  {/* Preparation notes input */}
                  {!hasAssignee && bounty.status === "ready_for_preparation" && (
                    <div className="mb-4">
                      <h5 className="text-xs text-zinc-500 uppercase mb-2">Preparation Notes (optional)</h5>
                      <textarea
                        value={preparationNotes[bounty.id] || ""}
                        onChange={e => setPreparationNotes(prev => ({
                          ...prev,
                          [bounty.id]: e.target.value
                        }))}
                        placeholder="Describe your preparation plan..."
                        className="w-full bg-black/50 border border-zinc-700 rounded px-3 py-2 text-xs text-white h-20 resize-none focus:border-orange-500/50 focus:outline-none"
                      />
                    </div>
                  )}

                  {/* Action buttons */}
                  <div className="flex gap-2">
                    {/* Claim for preparation */}
                    {bounty.status === "ready_for_preparation" && !hasAssignee && (
                      <button
                        onClick={() => handleClaimPreparation(bounty.id)}
                        disabled={isSubmitting === bounty.id}
                        className="flex-1 bg-orange-600 hover:bg-orange-500 disabled:bg-zinc-700 text-white px-4 py-2 rounded-lg flex items-center justify-center gap-2 text-sm font-medium transition-colors"
                      >
                        {isSubmitting === bounty.id ? (
                          <>
                            <Loader2 className="w-4 h-4 animate-spin" />
                            Claiming...
                          </>
                        ) : (
                          <>
                            <Eye className="w-4 h-4" />
                            Claim for Preparation
                          </>
                        )}
                      </button>
                    )}

                    {/* Activate (if deps completed and I'm the preparer) */}
                    {hasAssignee && depsCompleted && bounty.status === "ready_for_preparation" && (
                      <button
                        onClick={() => handleActivate(bounty.id)}
                        disabled={isSubmitting === bounty.id}
                        className="flex-1 bg-green-600 hover:bg-green-500 disabled:bg-zinc-700 text-white px-4 py-2 rounded-lg flex items-center justify-center gap-2 text-sm font-medium transition-colors"
                      >
                        {isSubmitting === bounty.id ? (
                          <>
                            <Loader2 className="w-4 h-4 animate-spin" />
                            Activating...
                          </>
                        ) : (
                          <>
                            <Play className="w-4 h-4" />
                            Start Working
                          </>
                        )}
                      </button>
                    )}

                    {/* View details */}
                    <button
                      onClick={() => onViewDetails?.(bounty.id)}
                      className="px-4 py-2 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded-lg flex items-center gap-2 text-sm transition-colors"
                    >
                      <FileText className="w-4 h-4" />
                      Details
                    </button>
                  </div>

                  {/* Warning message */}
                  {bounty.status === "ready_for_preparation" && !depsCompleted && (
                    <div className="mt-3 p-2 bg-orange-500/10 border border-orange-500/30 rounded-lg flex items-start gap-2">
                      <AlertTriangle className="w-4 h-4 text-orange-400 flex-shrink-0 mt-0.5" />
                      <p className="text-xs text-orange-300">
                        You can prepare and study the codebase, but <strong>cannot submit code</strong> until all dependencies are completed.
                      </p>
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
