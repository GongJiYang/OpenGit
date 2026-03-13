"use client";

import { useState, useEffect } from "react";
import { useParams, useRouter } from "next/navigation";
import {
    Settings, Cloud, Server, Zap, Rocket, Check, AlertCircle,
    ChevronRight, Save, Cpu, Clock, Shield
} from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "/api";

type ExecutionMode = "e2b_sandbox" | "shared_local" | "yolo_mode" | "self_hosted";

interface Runner {
    id: string;
    name: string;
    status: "online" | "offline" | "busy" | "disabled" | "banned";
    cpu_cores: number | null;
    memory_gb: number | null;
    os_type: string | null;
    total_jobs_completed: number;
    reputation_score: number;
    last_heartbeat_at: string | null;
}

interface RepoConfig {
    execution_mode: ExecutionMode;
    preferred_runner_ids: string[];
    e2b_budget_limit: number;
    yolo_require_human_review: boolean;
}

const EXECUTION_MODES = [
    {
        id: "e2b_sandbox" as ExecutionMode,
        icon: Cloud,
        title: "☁️ 官方沙箱",
        subtitle: "E2B Sandbox",
        description: "极度安全，按秒计费，与外网隔离",
        cost: "消耗 Bounty 预算 10%",
        useCase: "恶意代码高风险项目",
        color: "blue",
    },
    {
        id: "shared_local" as ExecutionMode,
        icon: Server,
        title: "🏢 平台公共机",
        subtitle: "Shared Local",
        description: "AgentHub 官方免费服务器",
        cost: "免费",
        useCase: "预算为零的小项目",
        status: "🟢 排队: 12 任务",
        color: "green",
    },
    {
        id: "yolo_mode" as ExecutionMode,
        icon: Zap,
        title: "⚡ YOLO 模式",
        subtitle: "Skip Testing",
        description: "跳过测试，直接人工 Review",
        cost: "免费 (极高风险)",
        useCase: "紧急修复",
        warning: "⚠️ 全靠人工审查",
        color: "yellow",
    },
    {
        id: "self_hosted" as ExecutionMode,
        icon: Rocket,
        title: "🚀 自托管节点",
        subtitle: "Self-Hosted",
        description: "接入自己的服务器或社区算力",
        cost: "赞助商分成 20%",
        useCase: "有闲置服务器",
        featured: true,
        color: "purple",
    },
];

export default function RepoSettingsPage() {
    const params = useParams();
    const repoId = params.id as string;

    const [mode, setMode] = useState<ExecutionMode>("shared_local");
    const [runners, setRunners] = useState<Runner[]>([]);
    const [saving, setSaving] = useState(false);
    const [yoloReview, setYoloReview] = useState(true);
    const [e2bBudget, setE2bBudget] = useState(1000);

    useEffect(() => {
        fetchRunners();
    }, []);

    const fetchRunners = async () => {
        try {
            const res = await fetch(`${API_BASE}/api/v1/runners`, {
                headers: { "X-User-Id": "demo-user" }
            });
            if (res.ok) {
                setRunners(await res.json());
            }
        } catch (e) {
            console.error(e);
        }
    };

    const handleSave = async () => {
        setSaving(true);
        await new Promise(r => setTimeout(r, 1000));
        setSaving(false);
    };

    const getStatusBadge = (status: string) => {
        const colors: Record<string, string> = {
            online: "bg-green-500/20 text-green-400",
            busy: "bg-yellow-500/20 text-yellow-400",
            offline: "bg-zinc-500/20 text-zinc-400",
        };
        return colors[status] || "bg-zinc-500/20 text-zinc-500";
    };

    return (
        <div className="space-y-6">
            <div className="flex items-center gap-3">
                <Settings className="w-6 h-6 text-purple-400" />
                <h1 className="text-2xl font-bold text-white">仓库设置</h1>
            </div>
            <p className="text-zinc-400">配置 CI/CD 测试环境</p>

            {/* Execution Mode Cards */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {EXECUTION_MODES.map((m) => {
                    const Icon = m.icon;
                    const isSelected = mode === m.id;
                    const colorMap: Record<string, string> = {
                        blue: "bg-blue-500/20 text-blue-400 border-blue-500/30",
                        green: "bg-green-500/20 text-green-400 border-green-500/30",
                        yellow: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30",
                        purple: "bg-purple-500/20 text-purple-400 border-purple-500/30",
                    };

                    return (
                        <button
                            key={m.id}
                            onClick={() => setMode(m.id)}
                            className={`glass-panel p-5 rounded-xl text-left transition-all w-full
                                ${isSelected ? "ring-2 ring-purple-500" : "hover:border-purple-500/30"}
                                ${m.featured ? "border border-purple-500/30" : ""}`}
                        >
                            <div className="flex items-start gap-4">
                                <div className={`p-2 rounded-lg ${colorMap[m.color]}`}>
                                    <Icon className="w-5 h-5" />
                                </div>
                                <div className="flex-1">
                                    <h3 className="font-semibold text-white">{m.title}</h3>
                                    <p className="text-sm text-zinc-400">{m.description}</p>
                                    <div className="flex items-center gap-4 mt-2 text-xs">
                                        <span className="text-zinc-500">费用: <span className="text-white">{m.cost}</span></span>
                                        <span className="text-zinc-500">适用: <span className="text-white">{m.useCase}</span></span>
                                    </div>
                                    {m.status && <p className="text-xs text-green-400 mt-1">{m.status}</p>}
                                    {m.warning && <p className="text-xs text-yellow-400 mt-1">{m.warning}</p>}
                                </div>
                                {isSelected && <Check className="w-5 h-5 text-purple-400 flex-shrink-0" />}
                            </div>
                        </button>
                    );
                })}
            </div>

            {/* Self-Hosted: Runner List */}
            {mode === "self_hosted" && (
                <div className="glass-panel rounded-xl p-6">
                    <h3 className="text-lg font-semibold text-white mb-4">当前连接节点</h3>
                    {runners.length === 0 ? (
                        <div className="text-center py-8 text-zinc-500">
                            <Server className="w-8 h-8 mx-auto mb-2" />
                            <p>暂无已注册的 Runner</p>
                            <p className="text-sm">前往 Runner 管理页添加服务器</p>
                        </div>
                    ) : (
                        <div className="space-y-2">
                            {runners.map((runner) => (
                                <div key={runner.id} className="flex items-center justify-between p-3 bg-zinc-800/50 rounded-lg">
                                    <div className="flex items-center gap-3">
                                        <div className={`w-2 h-2 rounded-full ${
                                            runner.status === "online" ? "bg-green-400" :
                                            runner.status === "busy" ? "bg-yellow-400" : "bg-zinc-400"
                                        }`} />
                                        <div>
                                            <p className="font-medium text-white">{runner.name}</p>
                                            <p className="text-xs text-zinc-400">
                                                {runner.cpu_cores} cores · {runner.os_type}
                                            </p>
                                        </div>
                                    </div>
                                    <span className={`text-xs px-2 py-1 rounded ${getStatusBadge(runner.status)}`}>
                                        {runner.status}
                                    </span>
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            )}

            {/* YOLO Mode: Require Review */}
            {mode === "yolo_mode" && (
                <div className="glass-panel rounded-xl p-6 border-yellow-500/30">
                    <div className="flex items-start gap-3">
                        <AlertCircle className="w-5 h-5 text-yellow-400 flex-shrink-0" />
                        <div>
                            <h3 className="text-lg font-semibold text-white">安全设置</h3>
                            <label className="flex items-center gap-2 mt-3">
                                <input
                                    type="checkbox"
                                    checked={yoloReview}
                                    onChange={(e) => setYoloReview(e.target.checked)}
                                    className="rounded"
                                />
                                <span className="text-sm text-zinc-300">强制要求人工 Review (推荐)</span>
                            </label>
                        </div>
                    </div>
                </div>
            )}

            {/* E2B Budget */}
            {mode === "e2b_sandbox" && (
                <div className="glass-panel rounded-xl p-6">
                    <h3 className="text-lg font-semibold text-white mb-4">E2B 预算设置</h3>
                    <div className="flex items-center gap-4">
                        <input
                            type="number"
                            value={e2bBudget}
                            onChange={(e) => setE2bBudget(parseInt(e.target.value) || 0)}
                            className="flex-1 bg-zinc-800 border border-zinc-700 rounded-lg px-4 py-2 text-white"
                        />
                        <span className="text-zinc-400">美分 (cents)</span>
                    </div>
                    <p className="text-xs text-zinc-500 mt-2">余额将在每次测试后自动扣除</p>
                </div>
            )}

            {/* Save Button */}
            <div className="flex justify-end">
                <button
                    onClick={handleSave}
                    disabled={saving}
                    className="px-6 py-2 bg-purple-500 hover:bg-purple-600 disabled:bg-zinc-700 text-white font-medium rounded-lg flex items-center gap-2 transition-colors"
                >
                    {saving ? (
                        <span className="flex items-center gap-2">
                            <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                            保存中...
                        </span>
                    ) : (
                        <>
                            <Save className="w-4 h-4" />
                            保存设置
                        </>
                    )}
                </button>
            </div>
        </div>
    );
}
