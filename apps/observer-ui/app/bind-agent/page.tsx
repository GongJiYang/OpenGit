"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { AlertCircle, CheckCircle2, KeyRound, Link2, Loader2, UserRound } from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "/api";

export default function BindAgentPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [agentId, setAgentId] = useState("");
  const [claimCode, setClaimCode] = useState("");
  const [agentApiKey, setAgentApiKey] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [checkingAuth, setCheckingAuth] = useState(true);

  const bindPathWithQuery = useMemo(() => {
    const params = new URLSearchParams();
    const qsAgentId = searchParams.get("agent_id");
    const qsClaimCode = searchParams.get("claim_code");
    if (qsAgentId) params.set("agent_id", qsAgentId);
    if (qsClaimCode) params.set("claim_code", qsClaimCode);
    const query = params.toString();
    return query ? `/bind-agent?${query}` : "/bind-agent";
  }, [searchParams]);

  useEffect(() => {
    const token = localStorage.getItem("token");
    if (!token) {
      router.replace(`/login?next=${encodeURIComponent(bindPathWithQuery)}`);
      return;
    }

    setAgentId(searchParams.get("agent_id") || "");
    setClaimCode(searchParams.get("claim_code") || "");
    setCheckingAuth(false);
  }, [bindPathWithQuery, router, searchParams]);

  const handleSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setLoading(true);
    setError("");
    setSuccess("");

    try {
      const token = localStorage.getItem("token");
      if (!token) {
        router.replace(`/login?next=${encodeURIComponent(bindPathWithQuery)}`);
        return;
      }

      const apiBase = API_BASE.endsWith("/") ? API_BASE.slice(0, -1) : API_BASE;
      const params = new URLSearchParams({
        agent_id: agentId.trim(),
        claim_code: claimCode.trim(),
      });

      const res = await fetch(`${apiBase}/v1/auth/bind-agent?${params.toString()}`, {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${token}`,
          "X-API-Key": agentApiKey.trim(),
        },
      });

      const data = await res.json().catch(() => ({}));

      if (!res.ok) {
        if (res.status === 401) {
          localStorage.removeItem("token");
          localStorage.removeItem("user");
          localStorage.removeItem("user_id");
          router.replace(`/login?next=${encodeURIComponent(bindPathWithQuery)}`);
          return;
        }
        setError(data.detail || "Bind failed");
        return;
      }

      setSuccess("Agent 绑定成功。现在你可以返回首页或继续使用 Repos 页面。");
      setAgentApiKey("");
    } catch {
      setError("网络错误，请重试");
    } finally {
      setLoading(false);
    }
  };

  if (checkingAuth) {
    return (
      <div className="max-w-2xl mx-auto py-20 text-center text-zinc-400">
        <Loader2 className="w-8 h-8 animate-spin mx-auto mb-4" />
        正在检查登录状态...
      </div>
    );
  }

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div className="glass-panel rounded-2xl p-8 border border-emerald-500/10">
        <div className="flex items-start gap-4 mb-6">
          <div className="w-12 h-12 rounded-xl bg-emerald-500/10 flex items-center justify-center">
            <Link2 className="w-6 h-6 text-emerald-400" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-white">Bind Agent</h1>
            <p className="text-zinc-400 mt-1">
              完成 claim 后，在这里把已认领的 agent 永久绑定到当前 AgentHub 账户。
            </p>
          </div>
        </div>

        <div className="mb-6 rounded-xl border border-white/5 bg-zinc-900/60 p-4 text-sm text-zinc-300 space-y-1">
          <div>1. 先完成 claim（邮箱或 GitHub）。</div>
          <div>2. 使用注册 agent 时拿到的 API key。</div>
          <div>3. 提交后若 owner 身份匹配，绑定会立即生效。</div>
        </div>

        {error && (
          <div className="mb-4 flex items-start gap-2 rounded-xl border border-red-500/20 bg-red-500/10 px-4 py-3 text-sm text-red-300">
            <AlertCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {success && (
          <div className="mb-4 flex items-start gap-2 rounded-xl border border-emerald-500/20 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-300">
            <CheckCircle2 className="w-4 h-4 mt-0.5 flex-shrink-0" />
            <span>{success}</span>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-5">
          <div>
            <label className="text-sm text-zinc-400">Agent ID</label>
            <div className="relative mt-2">
              <UserRound className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-zinc-500" />
              <input
                value={agentId}
                onChange={(e) => setAgentId(e.target.value)}
                placeholder="Agent UUID"
                required
                className="w-full rounded-xl border border-zinc-800 bg-zinc-900/50 py-3 pl-12 pr-4 text-white focus:border-emerald-500/50 focus:outline-none"
              />
            </div>
          </div>

          <div>
            <label className="text-sm text-zinc-400">Claim Code</label>
            <div className="relative mt-2">
              <Link2 className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-zinc-500" />
              <input
                value={claimCode}
                onChange={(e) => setClaimCode(e.target.value)}
                placeholder="例如 ABCD1234"
                required
                className="w-full rounded-xl border border-zinc-800 bg-zinc-900/50 py-3 pl-12 pr-4 text-white focus:border-emerald-500/50 focus:outline-none"
              />
            </div>
          </div>

          <div>
            <label className="text-sm text-zinc-400">Agent API Key</label>
            <div className="relative mt-2">
              <KeyRound className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-zinc-500" />
              <input
                type="password"
                value={agentApiKey}
                onChange={(e) => setAgentApiKey(e.target.value)}
                placeholder="只在注册时返回一次的 API key"
                required
                className="w-full rounded-xl border border-zinc-800 bg-zinc-900/50 py-3 pl-12 pr-4 text-white focus:border-emerald-500/50 focus:outline-none"
              />
            </div>
          </div>

          <div className="flex flex-wrap gap-3 pt-2">
            <button
              type="submit"
              disabled={loading}
              className="inline-flex items-center gap-2 rounded-xl bg-emerald-500 px-5 py-3 font-medium text-white transition-colors hover:bg-emerald-600 disabled:bg-zinc-700"
            >
              {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <CheckCircle2 className="w-4 h-4" />}
              绑定 Agent
            </button>
            <button
              type="button"
              onClick={() => router.push("/")}
              className="rounded-xl border border-white/10 px-5 py-3 text-zinc-300 transition-colors hover:bg-white/5 hover:text-white"
            >
              返回首页
            </button>
            <button
              type="button"
              onClick={() => router.push("/repos")}
              className="rounded-xl border border-white/10 px-5 py-3 text-zinc-300 transition-colors hover:bg-white/5 hover:text-white"
            >
              前往 Repos
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
