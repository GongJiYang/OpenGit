"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Terminal, Mail, Lock, ArrowRight, Github, AlertCircle } from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "/api";

export default function LoginPage() {
    const router = useRouter();
    const [mode, setMode] = useState<"login" | "register">("login");
    const [email, setEmail] = useState("");
    const [password, setPassword] = useState("");
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setLoading(true);
        setError("");

        try {
            const endpoint = mode === "login" ? "/v1/auth/login" : "/v1/auth/register";
            const res = await fetch(`${API_BASE}${endpoint}`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ email, password }),
            });

            const data = await res.json();

            if (!res.ok) {
                setError(data.detail || "请求失败");
                return;
            }

            // Save token
            localStorage.setItem("token", data.access_token);
            localStorage.setItem("user", JSON.stringify(data.user));
            localStorage.setItem("user_id", data.user.id);

            const next = new URLSearchParams(window.location.search).get("next");
            router.push(next || "/");
        } catch {
            setError("网络错误，请重试");
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="min-h-screen flex items-center justify-center px-4">
            {/* Background Effect */}
            <div className="fixed inset-0 overflow-hidden pointer-events-none">
                <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-emerald-500/10 rounded-full blur-3xl" />
                <div className="absolute bottom-1/4 right-1/4 w-96 h-96 bg-purple-500/10 rounded-full blur-3xl" />
            </div>

            <div className="relative w-full max-w-md">
                {/* Logo */}
                <div className="text-center mb-8">
                    <div className="inline-flex items-center gap-3 mb-4">
                        <div className="w-12 h-12 rounded-xl bg-emerald-500/20 flex items-center justify-center">
                            <Terminal className="w-6 h-6 text-emerald-400" />
                        </div>
                        <span className="text-2xl font-bold text-white">AgentHub</span>
                    </div>
                    <p className="text-zinc-400">
                        {mode === "login" ? "登录你的账户" : "创建新账户"}
                    </p>
                </div>

                {/* Form Card */}
                <div className="glass-panel rounded-2xl p-8">
                    <form onSubmit={handleSubmit} className="space-y-6">
                        {/* Error */}
                        {error && (
                            <div className="flex items-center gap-2 px-4 py-3 bg-red-500/10 border border-red-500/20 rounded-lg text-red-400 text-sm">
                                <AlertCircle className="w-4 h-4 flex-shrink-0" />
                                {error}
                            </div>
                        )}

                        {/* Email */}
                        <div className="space-y-2">
                            <label className="text-sm text-zinc-400">邮箱</label>
                            <div className="relative">
                                <Mail className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-zinc-500" />
                                <input
                                    type="email"
                                    value={email}
                                    onChange={(e) => setEmail(e.target.value)}
                                    className="w-full bg-zinc-900/50 border border-zinc-800 rounded-xl py-3 pl-12 pr-4 text-white placeholder-zinc-600 focus:border-emerald-500/50 focus:outline-none transition-colors"
                                    placeholder="you@example.com"
                                    required
                                />
                            </div>
                        </div>

                        {/* Password */}
                        <div className="space-y-2">
                            <label className="text-sm text-zinc-400">密码</label>
                            <div className="relative">
                                <Lock className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-zinc-500" />
                                <input
                                    type="password"
                                    value={password}
                                    onChange={(e) => setPassword(e.target.value)}
                                    className="w-full bg-zinc-900/50 border border-zinc-800 rounded-xl py-3 pl-12 pr-4 text-white placeholder-zinc-600 focus:border-emerald-500/50 focus:outline-none transition-colors"
                                    placeholder="••••••••"
                                    required
                                    minLength={6}
                                />
                            </div>
                        </div>

                        {/* Submit */}
                        <button
                            type="submit"
                            disabled={loading}
                            className="w-full bg-emerald-500 hover:bg-emerald-600 disabled:bg-zinc-700 text-white font-medium py-3 px-4 rounded-xl transition-colors flex items-center justify-center gap-2"
                        >
                            {loading ? (
                                <div className="w-5 h-5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                            ) : (
                                <>
                                    {mode === "login" ? "登录" : "注册"}
                                    <ArrowRight className="w-4 h-4" />
                                </>
                            )}
                        </button>
                    </form>

                    {/* Divider */}
                    <div className="relative my-6">
                        <div className="absolute inset-0 flex items-center">
                            <div className="w-full border-t border-zinc-800" />
                        </div>
                        <div className="relative flex justify-center text-sm">
                            <span className="px-4 bg-zinc-900 text-zinc-500">或者</span>
                        </div>
                    </div>

                    {/* OAuth */}
                    <button className="w-full bg-zinc-800 hover:bg-zinc-700 text-white font-medium py-3 px-4 rounded-xl transition-colors flex items-center justify-center gap-2">
                        <Github className="w-5 h-5" />
                        使用 GitHub 登录
                    </button>

                    {/* Switch Mode */}
                    <p className="mt-6 text-center text-sm text-zinc-500">
                        {mode === "login" ? (
                            <>
                                没有账户？{" "}
                                <button
                                    onClick={() => setMode("register")}
                                    className="text-emerald-400 hover:text-emerald-300"
                                >
                                    立即注册
                                </button>
                            </>
                        ) : (
                            <>
                                已有账户？{" "}
                                <button
                                    onClick={() => setMode("login")}
                                    className="text-emerald-400 hover:text-emerald-300"
                                >
                                    立即登录
                                </button>
                            </>
                        )}
                    </p>
                </div>
            </div>
        </div>
    );
}
