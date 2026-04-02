"use client";

import { DollarSign, Clock, GitBranch, Link2, Shield, User, X } from "lucide-react";

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
}

interface BountyDetailDrawerProps {
  bounty: Bounty | null;
  bounties: Bounty[];
  onClose: () => void;
}

const STATUS_BADGE_CLASS: Record<string, string> = {
  pending: "bg-zinc-500/10 text-zinc-400 border border-zinc-500/20",
  ready_for_preparation: "bg-orange-500/10 text-orange-400 border border-orange-500/20",
  open: "bg-green-500/10 text-green-400 border border-green-500/20",
  in_progress: "bg-blue-500/10 text-blue-400 border border-blue-500/20",
  submitted: "bg-yellow-500/10 text-yellow-400 border border-yellow-500/20",
  completed: "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20",
  cancelled: "bg-red-500/10 text-red-400 border border-red-500/20",
};

export default function BountyDetailDrawer({ bounty, bounties, onClose }: BountyDetailDrawerProps) {
  if (!bounty) return null;

  const dependencies = (bounty.dependencies || []).map((dependencyId) => {
    const matched = bounties.find((item) => item.id === dependencyId);
    return {
      id: dependencyId,
      title: matched?.title || dependencyId,
      status: matched?.status || "unknown",
    };
  });

  const statusBadge = STATUS_BADGE_CLASS[bounty.status] || STATUS_BADGE_CLASS.pending;

  return (
    <div className="fixed inset-0 z-50" onClick={onClose}>
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" />

      <aside
        className="absolute inset-y-0 right-0 w-full max-w-2xl border-l border-zinc-800 bg-zinc-950/95 shadow-2xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex h-full flex-col">
          <div className="flex items-start justify-between border-b border-zinc-800 px-6 py-5">
            <div className="min-w-0 flex-1">
              <div className="mb-2 flex items-center gap-2">
                <span className={`text-xs px-2 py-0.5 rounded-full uppercase font-medium ${statusBadge}`}>
                  {bounty.status.replace("_", " ")}
                </span>
                {bounty.track && (
                  <span className="text-xs bg-purple-500/10 text-purple-400 border border-purple-500/20 px-2 py-0.5 rounded-full">
                    {bounty.track}
                  </span>
                )}
              </div>
              <h2 className="text-xl font-bold text-white break-words">{bounty.title}</h2>
            </div>
            <button
              onClick={onClose}
              className="ml-4 rounded-lg border border-zinc-700 p-2 text-zinc-400 hover:border-zinc-500 hover:text-white"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          <div className="flex-1 space-y-6 overflow-y-auto px-6 py-5">
            <section>
              <h3 className="mb-2 text-xs uppercase tracking-wider text-zinc-500">Description</h3>
              <p className="text-sm leading-6 text-zinc-300 whitespace-pre-wrap">
                {bounty.description || "No description provided."}
              </p>
            </section>

            <section>
              <h3 className="mb-2 text-xs uppercase tracking-wider text-zinc-500">Overview</h3>
              <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                <div className="glass-panel rounded-lg p-3">
                  <div className="text-xs text-zinc-500">Reward</div>
                  <div className="mt-1 flex items-center gap-1 text-lg font-mono text-emerald-400">
                    <DollarSign className="h-4 w-4" />
                    {bounty.reward}
                  </div>
                </div>

                <div className="glass-panel rounded-lg p-3">
                  <div className="text-xs text-zinc-500">Required role</div>
                  <div className="mt-1 flex items-center gap-1 text-sm text-purple-400">
                    <Shield className="h-4 w-4" />
                    {bounty.required_role}
                  </div>
                </div>

                <div className="glass-panel rounded-lg p-3">
                  <div className="text-xs text-zinc-500">Repository</div>
                  <div className="mt-1 flex items-center gap-1 text-sm text-zinc-300">
                    <GitBranch className="h-4 w-4 text-zinc-500" />
                    {bounty.repo_name}
                  </div>
                </div>

                <div className="glass-panel rounded-lg p-3">
                  <div className="text-xs text-zinc-500">Estimate</div>
                  <div className="mt-1 flex items-center gap-1 text-sm text-zinc-300">
                    <Clock className="h-4 w-4 text-zinc-500" />
                    {bounty.estimated_hours ? `${bounty.estimated_hours}h` : "Not set"}
                  </div>
                </div>
              </div>
            </section>

            <section>
              <h3 className="mb-2 text-xs uppercase tracking-wider text-zinc-500">Assignment</h3>
              <div className="glass-panel rounded-lg p-3 text-sm text-zinc-300 flex items-center gap-2">
                <User className="h-4 w-4 text-zinc-500" />
                {bounty.assignee || "Unassigned"}
              </div>
            </section>

            <section>
              <h3 className="mb-2 text-xs uppercase tracking-wider text-zinc-500">Dependencies</h3>
              {dependencies.length === 0 ? (
                <div className="glass-panel rounded-lg p-3 text-sm text-zinc-500">No dependencies</div>
              ) : (
                <div className="space-y-2">
                  {dependencies.map((dependency) => (
                    <div key={dependency.id} className="glass-panel rounded-lg p-3">
                      <div className="flex items-center justify-between gap-2">
                        <div className="min-w-0 flex items-center gap-2 text-sm text-zinc-300">
                          <Link2 className="h-4 w-4 text-zinc-500 shrink-0" />
                          <span className="truncate">{dependency.title}</span>
                        </div>
                        <span className="text-xs text-zinc-500 whitespace-nowrap">{dependency.status}</span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </section>
          </div>
        </div>
      </aside>
    </div>
  );
}
