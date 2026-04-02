"use client";

import { useState, useEffect } from "react";
import {
  Sparkles,
  Star,
  TrendingUp,
  Clock,
  Target,
  Loader2,
  UserPlus,
  CheckCircle,
  AlertCircle,
  ChevronDown,
  ChevronUp,
  Zap,
  Award
} from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "/api";

interface AgentRecommendation {
  agent_id: string;
  agent_name: string;
  role: string;
  match_score: number;
  match_breakdown: {
    skill_match: number;
    availability: number;
    performance: number;
    preference: number;
  };
  current_active_tasks: number;
  max_concurrent_tasks: number;
  reliability_tier: string;
  matched_skills: string[];
  overall_score: number | null;
}

interface TaskRecommendationResponse {
  bounty_id: string;
  bounty_title: string;
  required_role: string;
  track: string | null;
  recommendations: AgentRecommendation[];
  total_agents_evaluated: number;
}

interface AgentRecommendationCardProps {
  bountyId: string;
  onAssigned?: (agentId: string, agentName: string) => void;
}

const TIER_STYLES: Record<string, { color: string; label: string }> = {
  platinum: { color: "text-cyan-400 bg-cyan-500/10", label: "Platinum" },
  gold: { color: "text-yellow-400 bg-yellow-500/10", label: "Gold" },
  silver: { color: "text-zinc-300 bg-zinc-500/10", label: "Silver" },
  bronze: { color: "text-orange-500 bg-orange-500/10", label: "Bronze" },
  new: { color: "text-zinc-500 bg-zinc-800", label: "New" },
};

export default function AgentRecommendationCard({ bountyId, onAssigned }: AgentRecommendationCardProps) {
  const [recommendations, setRecommendations] = useState<TaskRecommendationResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [assigning, setAssigning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(true);
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null);

  const fetchRecommendations = async () => {
    setLoading(true);
    setError(null);

    try {
      const res = await fetch(`${API_BASE}/v1/assignment/bounties/${bountyId}/recommend`);
      if (!res.ok) {
        throw new Error(`Failed to fetch recommendations: ${res.status}`);
      }
      const data = await res.json();
      setRecommendations(data);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load recommendations");
    } finally {
      setLoading(false);
    }
  };

  const handleAutoAssign = async (agentId?: string) => {
    setAssigning(true);
    setError(null);

    try {
      const url = agentId
        ? `${API_BASE}/v1/assignment/bounties/${bountyId}/assign/${agentId}`
        : `${API_BASE}/v1/assignment/bounties/${bountyId}/auto-assign`;

      const res = await fetch(url, { method: "POST" });

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || `Assignment failed: ${res.status}`);
      }

      const data = await res.json();

      if (onAssigned && data.assigned_to) {
        onAssigned(data.assigned_to, data.agent_name || "Agent");
      }

      // Refresh recommendations after assignment
      await fetchRecommendations();

    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to assign");
    } finally {
      setAssigning(false);
    }
  };

  useEffect(() => {
    if (bountyId) {
      fetchRecommendations();
    }
  }, [bountyId]);

  const getScoreColor = (score: number) => {
    if (score >= 0.8) return "text-emerald-400";
    if (score >= 0.6) return "text-blue-400";
    if (score >= 0.4) return "text-yellow-400";
    return "text-zinc-400";
  };

  const getScoreBg = (score: number) => {
    if (score >= 0.8) return "bg-emerald-500";
    if (score >= 0.6) return "bg-blue-500";
    if (score >= 0.4) return "bg-yellow-500";
    return "bg-zinc-500";
  };

  if (loading && !recommendations) {
    return (
      <div className="glass-panel rounded-xl p-4">
        <div className="flex items-center justify-center py-4">
          <Loader2 className="w-5 h-5 animate-spin text-zinc-500" />
        </div>
      </div>
    );
  }

  if (!recommendations) return null;

  const bestMatch = recommendations.recommendations[0];

  return (
    <div className="glass-panel rounded-xl overflow-hidden">
      {/* Header */}
      <div
        className="flex items-center justify-between p-4 cursor-pointer hover:bg-white/5 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-2">
          <Sparkles className="w-4 h-4 text-purple-400" />
          <h4 className="font-medium text-white text-sm">Smart Recommendations</h4>
          <span className="text-[10px] text-zinc-500 bg-zinc-800 px-1.5 py-0.5 rounded">
            {recommendations.recommendations.length} agents
          </span>
        </div>
        <div className="flex items-center gap-2">
          {bestMatch && (
            <span className={`text-xs font-mono ${getScoreColor(bestMatch.match_score)}`}>
              Best: {Math.round(bestMatch.match_score * 100)}%
            </span>
          )}
          {expanded ? (
            <ChevronUp className="w-4 h-4 text-zinc-500" />
          ) : (
            <ChevronDown className="w-4 h-4 text-zinc-500" />
          )}
        </div>
      </div>

      {expanded && (
        <div className="border-t border-zinc-800">
          {/* Error Display */}
          {error && (
            <div className="p-3 bg-red-500/10 border-b border-red-500/20">
              <div className="flex items-center gap-2 text-red-400 text-xs">
                <AlertCircle className="w-3 h-3" />
                {error}
              </div>
            </div>
          )}

          {/* Best Match Highlight */}
          {bestMatch && (
            <div className="p-4 bg-gradient-to-r from-purple-500/5 to-blue-500/5 border-b border-zinc-800">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <Award className="w-4 h-4 text-yellow-400" />
                  <span className="text-xs font-medium text-white">Best Match</span>
                </div>
                <button
                  onClick={() => handleAutoAssign(bestMatch.agent_id)}
                  disabled={assigning}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-purple-600 hover:bg-purple-500 text-white text-xs font-medium transition-colors disabled:opacity-50"
                >
                  {assigning ? (
                    <Loader2 className="w-3 h-3 animate-spin" />
                  ) : (
                    <UserPlus className="w-3 h-3" />
                  )}
                  Assign
                </button>
              </div>

              <div className="flex items-center gap-3">
                <div className="flex-1">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="font-medium text-white">{bestMatch.agent_name}</span>
                    <span className={`text-[10px] px-1.5 py-0.5 rounded ${TIER_STYLES[bestMatch.reliability_tier]?.color || "text-zinc-500"}`}>
                      {TIER_STYLES[bestMatch.reliability_tier]?.label || bestMatch.reliability_tier}
                    </span>
                  </div>

                  {/* Match Score Bar */}
                  <div className="flex items-center gap-2">
                    <div className="flex-1 h-2 bg-zinc-800 rounded-full overflow-hidden">
                      <div
                        className={`h-full ${getScoreBg(bestMatch.match_score)} transition-all duration-500`}
                        style={{ width: `${bestMatch.match_score * 100}%` }}
                      />
                    </div>
                    <span className={`text-sm font-mono font-bold ${getScoreColor(bestMatch.match_score)}`}>
                      {Math.round(bestMatch.match_score * 100)}%
                    </span>
                  </div>

                  {/* Matched Skills */}
                  {bestMatch.matched_skills.length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-2">
                      {bestMatch.matched_skills.slice(0, 5).map((skill, i) => (
                        <span
                          key={i}
                          className="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-400"
                        >
                          {skill}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* Other Recommendations */}
          {recommendations.recommendations.length > 1 && (
            <div className="divide-y divide-zinc-800/50">
              {recommendations.recommendations.slice(1, 4).map((agent, index) => (
                <div
                  key={agent.agent_id}
                  className={`p-3 hover:bg-white/5 transition-colors cursor-pointer ${
                    selectedAgent === agent.agent_id ? "bg-purple-500/10" : ""
                  }`}
                  onClick={() => setSelectedAgent(selectedAgent === agent.agent_id ? null : agent.agent_id)}
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-zinc-500">#{index + 2}</span>
                      <span className="text-sm text-white">{agent.agent_name}</span>
                      <span className={`text-[10px] px-1 py-0.5 rounded ${TIER_STYLES[agent.reliability_tier]?.color || "text-zinc-500"}`}>
                        {TIER_STYLES[agent.reliability_tier]?.label || agent.reliability_tier}
                      </span>
                    </div>
                    <div className="flex items-center gap-2">
                      <div className="w-16 h-1.5 bg-zinc-800 rounded-full overflow-hidden">
                        <div
                          className={`h-full ${getScoreBg(agent.match_score)}`}
                          style={{ width: `${agent.match_score * 100}%` }}
                        />
                      </div>
                      <span className={`text-xs font-mono ${getScoreColor(agent.match_score)}`}>
                        {Math.round(agent.match_score * 100)}%
                      </span>
                    </div>
                  </div>

                  {/* Expanded Details */}
                  {selectedAgent === agent.agent_id && (
                    <div className="mt-3 pt-3 border-t border-zinc-800">
                      <div className="grid grid-cols-4 gap-2 text-[10px] mb-2">
                        <div>
                          <div className="text-zinc-500">Skills</div>
                          <div className="text-white font-mono">{Math.round(agent.match_breakdown.skill_match * 100)}%</div>
                        </div>
                        <div>
                          <div className="text-zinc-500">Available</div>
                          <div className="text-white font-mono">{Math.round(agent.match_breakdown.availability * 100)}%</div>
                        </div>
                        <div>
                          <div className="text-zinc-500">Performance</div>
                          <div className="text-white font-mono">{Math.round(agent.match_breakdown.performance * 100)}%</div>
                        </div>
                        <div>
                          <div className="text-zinc-500">Preference</div>
                          <div className="text-white font-mono">{Math.round(agent.match_breakdown.preference * 100)}%</div>
                        </div>
                      </div>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleAutoAssign(agent.agent_id);
                        }}
                        disabled={assigning}
                        className="w-full py-1.5 rounded bg-zinc-800 hover:bg-zinc-700 text-white text-xs transition-colors disabled:opacity-50"
                      >
                        Assign to this agent
                      </button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* No Recommendations */}
          {recommendations.recommendations.length === 0 && (
            <div className="p-6 text-center">
              <AlertCircle className="w-8 h-8 text-zinc-600 mx-auto mb-2" />
              <p className="text-sm text-zinc-500">No matching agents available</p>
              <p className="text-xs text-zinc-600 mt-1">
                Try again later or assign manually
              </p>
            </div>
          )}

          {/* Footer */}
          <div className="p-3 border-t border-zinc-800 bg-black/20">
            <div className="flex items-center justify-between text-[10px] text-zinc-500">
              <span>Evaluated {recommendations.total_agents_evaluated} agents</span>
              <button
                onClick={() => handleAutoAssign()}
                disabled={assigning || recommendations.recommendations.length === 0}
                className="flex items-center gap-1 text-purple-400 hover:text-purple-300 disabled:text-zinc-600"
              >
                <Zap className="w-3 h-3" />
                Auto-assign best
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
