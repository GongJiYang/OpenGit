"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import {
    Server, Plus, Trash2, Copy, Check, Terminal, Cpu, Clock,
    Activity, AlertCircle, ExternalLink, LogIn, Loader2
} from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "/api";

interface Runner {
    id: string;
    name: string;
    status: "online" | "offline" | "busy" | "disabled" | "banned";
    cpu_cores: number | null;
    memory_gb: number | null;
    os_type: string | null;
    os_version: string | null;
    docker_version: string | null;
    total_jobs_completed: number;
    total_compute_seconds: number;
    total_earnings: number;
    reputation_score: number;
    last_heartbeat_at: string | null;
    created_at: string;
}

interface TokenInfo {
    token: string;
    expires_at: string;
    command: string;
}

export default function RunnersPage() {
    const router = useRouter();
    const [runners, setRunners] = useState<Runner[]>([]);
    const [loading, setLoading] = useState(true);
    const [showToken, setShowToken] = useState<TokenInfo | null>(null);
    const [copied, setCopied] = useState(false);
    const [isLoggedIn, setIsLoggedIn] = useState(false);

    useEffect(() => {
        const token = localStorage.getItem("token");
        setIsLoggedIn(!!token);
        if (token) {
            fetchRunners();
        } else {
            setLoading(false);
        }
    }, []);

    const getAuthHeaders = () => {
        const token = localStorage.getItem("token");
        const userId = localStorage.getItem("user_id");
        const headers: Record<string, string> = {
            "Content-Type": "application/json",
        };
        if (token) {
            headers["Authorization"] = `Bearer ${token}`;
        }
        if (userId) {
            headers["X-User-Id"] = userId;
        }
        return headers;
    };

    const fetchRunners = async () => {
        try {
            setLoading(true);
            const res = await fetch(`${API_BASE}/api/v1/runners`, {
                headers: getAuthHeaders()
            });
            if (res.ok) {
                setRunners(await res.json());
            } else if (res.status === 401) {
                localStorage.removeItem("token");
                setIsLoggedIn(false);
            }
        } catch (e) {
            console.error(e);
        } finally {
            setLoading(false);
        }
    };

    const requireAuth = () => {
        if (!isLoggedIn) {
            router.push("/login");
            return false;
        }
        return true;
    };

    const generateToken = async () => {
        if (!requireAuth()) return;

        try {
            const res = await fetch(`${API_BASE}/api/v1/runners/generate-token`, {
                method: "POST",
                headers: getAuthHeaders()
            });
            if (res.ok) {
                const data = await res.json();
                setShowToken(data);
            } else {
                const data = await res.json();
                alert(data.detail || "Failed to generate token");
            }
        } catch (e) {
            console.error(e);
            alert("Network error");
        }
    };

    const deleteRunner = async (runnerId: string) => {
        if (!requireAuth()) return;
        if (!confirm("确定要禁用此 Runner 吗？")) return;

        try {
            const res = await fetch(`${API_BASE}/api/v1/runners/${runnerId}`, {
                method: "DELETE",
                headers: getAuthHeaders()
            });
            if (res.ok) {
                fetchRunners();
            }
        } catch (e) {
            console.error(e);
        }
    };

    const copyToClipboard = (text: string) => {
        navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };

    const formatUptime = (seconds: number) => {
        const hours = Math.floor(seconds / 3600);
        const mins = Math.floor((seconds % 3600) / 60);
        if (hours > 0) return `${hours}h ${mins}m`;
        return `${mins}m`;
    };

    const getStatusColor = (status: string) => {
        const colors: Record<string, string> = {
            online: "text-green-400 bg-green-500/10 border-green-500/20",
            busy: "text-yellow-400 bg-yellow-500/10 border-yellow-500/20",
            offline: "text-zinc-400 bg-zinc-500/10 border-zinc-500/20",
            disabled: "text-zinc-500 bg-zinc-500/10 border-zinc-500/20",
            banned: "text-red-400 bg-red-500/10 border-red-500/20",
        };
        return colors[status] || "text-zinc-400 bg-zinc-500/10";
    };

    const getReputationColor = (score: number) => {
        if (score >= 80) return "text-emerald-400";
        if (score >= 50) return "text-yellow-400";
        return "text-red-400";
    };

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="flex items-start justify-between">
                <div>
                    <h1 className="text-3xl font-bold text-white flex items-center gap-3">
                        <Server className="w-8 h-8 text-purple-500" />
                        Runner 管理
                    </h1>
                    <p className="text-zinc-400 mt-2 max-w-xl">
                        <span className="text-purple-400 font-medium">自托管节点</span> -
                        连接你的服务器到 AgentHub，赚取算力分成
                        {!isLoggedIn && <span className="text-emerald-400 ml-2">Login to manage.</span>}
                    </p>
                </div>
                {isLoggedIn ? (
                    <button
                        onClick={generateToken}
                        className="px-4 py-2 bg-purple-500 hover:bg-purple-600 text-white font-medium rounded-lg flex items-center gap-2 transition-colors"
                    >
                        <Plus className="w-4 h-4" />
                        添加服务器
                    </button>
                ) : (
                    <button
                        onClick={() => router.push("/login")}
                        className="px-4 py-2 bg-zinc-700 hover:bg-zinc-600 text-white font-medium rounded-lg flex items-center gap-2 transition-colors"
                    >
                        <LogIn className="w-4 h-4" />
                        Login to Add
                    </button>
                )}
            </div>

            {/* Token Modal */}
            {showToken && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
                    <div className="glass-panel rounded-2xl p-6 max-w-xl w-full mx-4">
                        <h2 className="text-xl font-bold text-white mb-4 flex items-center gap-2">
                            <Terminal className="w-5 h-5 text-green-400" />
                            Runner 注册令牌
                        </h2>
                        <p className="text-yellow-400 text-sm mb-4 flex items-center gap-2">
                            <AlertCircle className="w-4 h-4" />
                            此令牌只显示一次，请立即复制保存！
                        </p>
                        <div className="bg-zinc-800 rounded-lg p-4 mb-4">
                            <code className="text-green-400 text-sm break-all">
                                {showToken.command}
                            </code>
                        </div>
                        <div className="flex gap-3">
                            <button
                                onClick={() => copyToClipboard(showToken.command)}
                                className="flex-1 px-4 py-2 bg-zinc-700 hover:bg-zinc-600 text-white rounded-lg flex items-center justify-center gap-2"
                            >
                                {copied ? <Check className="w-4 h-4" /> : <Copy className="w-4 h-4" />}
                                {copied ? "已复制" : "复制命令"}
                            </button>
                            <button
                                onClick={() => setShowToken(null)}
                                className="px-4 py-2 bg-purple-500 hover:bg-purple-600 text-white rounded-lg"
                            >
                                关闭
                            </button>
                        </div>
                        <p className="text-zinc-500 text-xs mt-4">
                            令牌有效期至: {new Date(showToken.expires_at).toLocaleString()}
                        </p>
                    </div>
                </div>
            )}

            {/* Runners List */}
            {loading ? (
                <div className="text-center py-16">
                    <Loader2 className="w-8 h-8 text-zinc-500 animate-spin mx-auto mb-4" />
                    <p className="text-zinc-500">Loading...</p>
                </div>
            ) : !isLoggedIn ? (
                <div className="glass-panel rounded-xl p-12 text-center">
                    <Server className="w-16 h-16 text-zinc-600 mx-auto mb-4" />
                    <h3 className="text-xl font-semibold text-white mb-2">Login Required</h3>
                    <p className="text-zinc-400 mb-6">
                        Login to view and manage your runners
                    </p>
                    <button
                        onClick={() => router.push("/login")}
                        className="px-6 py-3 bg-purple-500 hover:bg-purple-600 text-white font-medium rounded-lg inline-flex items-center gap-2"
                    >
                        <LogIn className="w-5 h-5" />
                        Go to Login
                    </button>
                </div>
            ) : runners.length === 0 ? (
                <div className="glass-panel rounded-xl p-12 text-center">
                    <Server className="w-16 h-16 text-zinc-600 mx-auto mb-4" />
                    <h3 className="text-xl font-semibold text-white mb-2">暂无 Runner</h3>
                    <p className="text-zinc-400 mb-6">
                        添加你的服务器到 AgentHub，开始赚取算力分成
                    </p>
                    <button
                        onClick={generateToken}
                        className="px-6 py-3 bg-purple-500 hover:bg-purple-600 text-white font-medium rounded-lg inline-flex items-center gap-2"
                    >
                        <Plus className="w-5 h-5" />
                        添加第一台服务器
                    </button>
                </div>
            ) : (
                <div className="space-y-4">
                    {runners.map((runner) => (
                        <div
                            key={runner.id}
                            className="glass-panel rounded-xl p-5 transition-all hover:border-purple-500/30"
                        >
                            <div className="flex items-start justify-between">
                                <div className="flex items-center gap-4">
                                    <div className="w-12 h-12 rounded-xl bg-purple-500/20 flex items-center justify-center">
                                        <Server className="w-6 h-6 text-purple-400" />
                                    </div>
                                    <div>
                                        <div className="flex items-center gap-3">
                                            <h3 className="text-lg font-bold text-white">{runner.name}</h3>
                                            <span className={`text-xs px-2 py-0.5 rounded-full border ${getStatusColor(runner.status)}`}>
                                                {runner.status}
                                            </span>
                                        </div>
                                        <div className="flex items-center gap-4 mt-1 text-sm text-zinc-400">
                                            {runner.os_type && (
                                                <span className="flex items-center gap-1">
                                                    <Terminal className="w-3 h-3" />
                                                    {runner.os_type} {runner.os_version}
                                                </span>
                                            )}
                                            {runner.docker_version && (
                                                <span>Docker {runner.docker_version}</span>
                                            )}
                                        </div>
                                    </div>
                                </div>
                                <div className="flex items-center gap-4">
                                    <div className="text-right">
                                        <p className={`text-2xl font-bold ${getReputationColor(runner.reputation_score)}`}>
                                            {runner.reputation_score}
                                        </p>
                                        <p className="text-xs text-zinc-500">信誉分</p>
                                    </div>
                                    <button
                                        onClick={() => deleteRunner(runner.id)}
                                        className="p-2 text-zinc-500 hover:text-red-400 hover:bg-red-500/10 rounded-lg transition-colors"
                                    >
                                        <Trash2 className="w-5 h-5" />
                                    </button>
                                </div>
                            </div>

                            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-4 pt-4 border-t border-zinc-800">
                                <div>
                                    <p className="text-xs text-zinc-500">完成任务</p>
                                    <p className="text-lg font-semibold text-white">{runner.total_jobs_completed}</p>
                                </div>
                                <div>
                                    <p className="text-xs text-zinc-500">累计运行</p>
                                    <p className="text-lg font-semibold text-white">
                                        {formatUptime(runner.total_compute_seconds)}
                                    </p>
                                </div>
                                <div>
                                    <p className="text-xs text-zinc-500">累计收益</p>
                                    <p className="text-lg font-semibold text-emerald-400">
                                        ${(runner.total_earnings / 100).toFixed(2)}
                                    </p>
                                </div>
                                <div>
                                    <p className="text-xs text-zinc-500">配置</p>
                                    <p className="text-lg font-semibold text-white">
                                        {runner.cpu_cores || "?"}C / {runner.memory_gb || "?"}G
                                    </p>
                                </div>
                            </div>

                            {runner.status === "banned" && (
                                <div className="mt-4 px-4 py-3 bg-red-500/10 border border-red-500/20 rounded-lg flex items-center gap-2">
                                    <AlertCircle className="w-4 h-4 text-red-400" />
                                    <span className="text-sm text-red-400">此节点已被封禁</span>
                                </div>
                            )}
                        </div>
                    ))}
                </div>
            )}

            {/* Info Card */}
            <div className="glass-panel rounded-xl p-6">
                <h3 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
                    <Activity className="w-5 h-5 text-purple-400" />
                    如何添加服务器？
                </h3>
                <ol className="space-y-3 text-zinc-300">
                    <li className="flex items-start gap-3">
                        <span className="flex-shrink-0 w-6 h-6 bg-purple-500/20 text-purple-400 rounded-full flex items-center justify-center text-sm">1</span>
                        <span>点击上方「添加服务器」按钮，生成注册令牌</span>
                    </li>
                    <li className="flex items-start gap-3">
                        <span className="flex-shrink-0 w-6 h-6 bg-purple-500/20 text-purple-400 rounded-full flex items-center justify-center text-sm">2</span>
                        <span>在你的服务器上安装 agenthub-runner</span>
                        <code className="text-xs bg-zinc-800 px-2 py-1 rounded">pip install agenthub-runner</code>
                    </li>
                    <li className="flex items-start gap-3">
                        <span className="flex-shrink-0 w-6 h-6 bg-purple-500/20 text-purple-400 rounded-full flex items-center justify-center text-sm">3</span>
                        <span>运行注册命令，</span>
                        <code className="text-xs bg-zinc-800 px-2 py-1 rounded">agenthub-runner start --token="你的令牌"</code>
                    </li>
                    <li className="flex items-start gap-3">
                        <span className="flex-shrink-0 w-6 h-6 bg-purple-500/20 text-purple-400 rounded-full flex items-center justify-center text-sm">4</span>
                        <span>服务器会自动连接到平台，开始接收任务</span>
                    </li>
                </ol>
                <p className="mt-4 text-sm text-zinc-500">
                    你的服务器将在 NAT 后面安全运行，平台不会主动连接你的服务器。
                </p>
            </div>
        </div>
    );
}
