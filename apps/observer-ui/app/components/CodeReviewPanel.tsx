"use client";

import { useState, useEffect } from "react";
import {
  GitPullRequest,
  CheckCircle,
  XCircle,
  Clock,
  User,
  FileText,
  ChevronDown,
  ChevronUp,
  AlertCircle,
  Loader2,
  Eye,
  RefreshCw
} from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "/api";

const getAuthHeaders = (): Record<string, string> => {
  const headers: Record<string, string> = {};
  if (typeof window !== "undefined") {
    const token = localStorage.getItem("token");
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }
  }
  return headers;
};

interface ReviewComment {
  author_id: string;
  content: string;
  line_number: number | null;
  created_at: string;
}

interface CodeReview {
  review_id: string;
  file_path: string;
  agent_id: string;
  reviewer_id: string | null;
  status: string;
  comments: ReviewComment[];
  created_at: string;
  updated_at: string;
}

interface CodeReviewPanelProps {
  reviews?: CodeReview[];
  onCreateReview?: (filePath: string) => void;
  onSubmitReview?: (reviewId: string, status: string, comments: string) => void;
}

const STATUS_CONFIG: Record<string, { color: string; bg: string; icon: typeof CheckCircle; label: string }> = {
  pending: { color: "text-yellow-400", bg: "bg-yellow-500/10", icon: Clock, label: "Pending" },
  approved: { color: "text-emerald-400", bg: "bg-emerald-500/10", icon: CheckCircle, label: "Approved" },
  rejected: { color: "text-red-400", bg: "bg-red-500/10", icon: XCircle, label: "Rejected" },
  changes_requested: { color: "text-orange-400", bg: "bg-orange-500/10", icon: AlertCircle, label: "Changes Requested" },
};

export default function CodeReviewPanel({
  reviews: externalReviews,
  onCreateReview,
  onSubmitReview
}: CodeReviewPanelProps) {
  const [reviews, setReviews] = useState<CodeReview[]>(externalReviews || []);
  const [loading, setLoading] = useState(false);
  const [expandedReview, setExpandedReview] = useState<string | null>(null);
  const [newComment, setNewComment] = useState("");
  const [newFilePath, setNewFilePath] = useState("");
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [isLoggedIn, setIsLoggedIn] = useState(false);
  const isExternallyManaged = externalReviews !== undefined;

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    setIsLoggedIn(Boolean(localStorage.getItem("token")));
  }, []);

  useEffect(() => {
    if (!isExternallyManaged && isLoggedIn) {
      fetchReviews();
    }
    if (!isExternallyManaged && !isLoggedIn) {
      setReviews([]);
    }
  }, [isExternallyManaged, isLoggedIn]);

  const fetchReviews = async () => {
    const headers = getAuthHeaders();
    if (!headers.Authorization) {
      setReviews([]);
      return;
    }

    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/v1/collaboration/reviews/reviewer/me`, {
        headers,
      });
      if (res.status === 401) {
        setReviews([]);
        return;
      }
      if (res.ok) {
        const data = await res.json();
        setReviews(data.reviews || []);
      }
    } catch (e) {
      console.error("Failed to fetch reviews:", e);
    } finally {
      setLoading(false);
    }
  };

  const handleCreateReview = async () => {
    if (!newFilePath.trim()) return;

    const reviewId = `review-${Date.now()}`;

    try {
      const res = await fetch(`${API_BASE}/v1/collaboration/reviews/create`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...getAuthHeaders() },
        body: JSON.stringify({
          review_id: reviewId,
          file_path: newFilePath
        })
      });

      if (res.ok) {
        setNewFilePath("");
        setShowCreateForm(false);
        fetchReviews();
        onCreateReview?.(newFilePath);
      }
    } catch (e) {
      console.error("Failed to create review:", e);
    }
  };

  const handleSubmitReview = async (reviewId: string, status: string) => {
    try {
      const res = await fetch(`${API_BASE}/v1/collaboration/reviews/${reviewId}/submit`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...getAuthHeaders() },
        body: JSON.stringify({
          status: status,
          comments: newComment ? [{ content: newComment }] : null
        })
      });

      if (res.ok) {
        setNewComment("");
        fetchReviews();
        onSubmitReview?.(reviewId, status, newComment);
      }
    } catch (e) {
      console.error("Failed to submit review:", e);
    }
  };

  useEffect(() => {
    if (isExternallyManaged) {
      setReviews(externalReviews || []);
    }
  }, [externalReviews, isExternallyManaged]);

  const pendingCount = reviews.filter(r => r.status === "pending").length;

  return (
    <div className="glass-panel rounded-xl overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b border-zinc-800">
        <h3 className="text-lg font-bold text-white flex items-center gap-2">
          <GitPullRequest className="w-5 h-5 text-purple-400" />
          Code Reviews
        </h3>
        <div className="flex items-center gap-2">
          {pendingCount > 0 && (
            <span className="text-xs text-yellow-400 bg-yellow-500/10 px-2 py-0.5 rounded-full">
              {pendingCount} pending
            </span>
          )}
          <button
            onClick={fetchReviews}
            disabled={loading || !isLoggedIn}
            className="p-1.5 rounded bg-zinc-800 text-zinc-400 hover:text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
          </button>
        </div>
      </div>

      {/* Create Review Button */}
      <div className="p-4 border-b border-zinc-800">
        {showCreateForm ? (
          <div className="space-y-2">
            <input
              type="text"
              value={newFilePath}
              onChange={e => setNewFilePath(e.target.value)}
              placeholder="File path for review..."
              className="w-full bg-black/50 border border-zinc-800 rounded px-3 py-2 text-sm text-white focus:border-purple-500/50 focus:outline-none"
            />
            <div className="flex gap-2">
              <button
                onClick={handleCreateReview}
                className="flex-1 py-2 bg-purple-600 hover:bg-purple-500 text-white text-sm rounded transition-colors"
              >
                Create Review
              </button>
              <button
                onClick={() => setShowCreateForm(false)}
                className="px-3 py-2 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-sm rounded transition-colors"
              >
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <button
            onClick={() => setShowCreateForm(true)}
            className="w-full py-2 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-sm rounded flex items-center justify-center gap-2 transition-colors"
          >
            <GitPullRequest className="w-4 h-4" />
            Request Review
          </button>
        )}
      </div>

      {/* Reviews List */}
      <div className="divide-y divide-zinc-800">
        {loading && reviews.length === 0 ? (
          <div className="p-8 text-center">
            <Loader2 className="w-6 h-6 animate-spin text-zinc-500 mx-auto" />
          </div>
        ) : reviews.length === 0 ? (
          <div className="p-8 text-center text-zinc-500">
            <Eye className="w-8 h-8 mx-auto mb-2 opacity-50" />
            <p className="text-sm">No code reviews</p>
          </div>
        ) : (
          reviews.map((review) => {
            const config = STATUS_CONFIG[review.status] || STATUS_CONFIG.pending;
            const StatusIcon = config.icon;
            const isExpanded = expandedReview === review.review_id;

            return (
              <div key={review.review_id} className="hover:bg-white/5 transition-colors">
                {/* Review Header */}
                <div
                  className="p-4 cursor-pointer"
                  onClick={() => setExpandedReview(isExpanded ? null : review.review_id)}
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3">
                      <div className={`p-2 rounded ${config.bg}`}>
                        <StatusIcon className={`w-4 h-4 ${config.color}`} />
                      </div>
                      <div>
                        <div className="text-sm font-medium text-white flex items-center gap-2">
                          <FileText className="w-3 h-3 text-zinc-500" />
                          <span className="font-mono text-xs truncate max-w-[200px]">
                            {review.file_path}
                          </span>
                        </div>
                        <div className="text-xs text-zinc-500 flex items-center gap-2 mt-1">
                          <User className="w-3 h-3" />
                          {review.agent_id.slice(0, 8)}
                          <span className="text-zinc-600">|</span>
                          <Clock className="w-3 h-3" />
                          {new Date(review.created_at).toLocaleDateString()}
                        </div>
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className={`text-xs px-2 py-0.5 rounded ${config.bg} ${config.color}`}>
                        {config.label}
                      </span>
                      {isExpanded ? (
                        <ChevronUp className="w-4 h-4 text-zinc-500" />
                      ) : (
                        <ChevronDown className="w-4 h-4 text-zinc-500" />
                      )}
                    </div>
                  </div>
                </div>

                {/* Expanded Details */}
                {isExpanded && (
                  <div className="px-4 pb-4 space-y-3">
                    {/* Comments */}
                    {review.comments.length > 0 && (
                      <div className="space-y-2">
                        <div className="text-xs text-zinc-500 uppercase">Comments</div>
                        {review.comments.map((comment, i) => (
                          <div key={i} className="bg-black/30 rounded p-3">
                            <div className="flex items-center gap-2 mb-1">
                              <span className="text-xs text-purple-400 font-mono">
                                {comment.author_id.slice(0, 8)}
                              </span>
                              {comment.line_number && (
                                <span className="text-[10px] text-zinc-600 bg-zinc-800 px-1 rounded">
                                  Line {comment.line_number}
                                </span>
                              )}
                            </div>
                            <p className="text-sm text-zinc-300">{comment.content}</p>
                          </div>
                        ))}
                      </div>
                    )}

                    {/* Add Comment & Actions */}
                    {review.status === "pending" && (
                      <div className="space-y-2">
                        <textarea
                          value={newComment}
                          onChange={e => setNewComment(e.target.value)}
                          placeholder="Add a comment..."
                          className="w-full bg-black/50 border border-zinc-800 rounded px-3 py-2 text-sm text-white h-20 resize-none focus:border-purple-500/50 focus:outline-none"
                        />
                        <div className="flex gap-2">
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              handleSubmitReview(review.review_id, "approved");
                            }}
                            className="flex-1 py-2 bg-emerald-600 hover:bg-emerald-500 text-white text-sm rounded flex items-center justify-center gap-1 transition-colors"
                          >
                            <CheckCircle className="w-4 h-4" />
                            Approve
                          </button>
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              handleSubmitReview(review.review_id, "changes_requested");
                            }}
                            className="flex-1 py-2 bg-orange-600 hover:bg-orange-500 text-white text-sm rounded flex items-center justify-center gap-1 transition-colors"
                          >
                            <AlertCircle className="w-4 h-4" />
                            Request Changes
                          </button>
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              handleSubmitReview(review.review_id, "rejected");
                            }}
                            className="px-4 py-2 bg-red-600 hover:bg-red-500 text-white text-sm rounded flex items-center justify-center gap-1 transition-colors"
                          >
                            <XCircle className="w-4 h-4" />
                            Reject
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
