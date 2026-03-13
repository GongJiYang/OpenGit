"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import {
    User, Mail, Lock, Shield, Bell, Palette, Save,
    ArrowLeft, Loader2, Check, AlertCircle
} from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "/api";

interface UserProfile {
    id: string;
    email: string;
    display_name: string;
    github_login?: string;
    avatar_url?: string;
    role: string;
    created_at: string;
}

export default function SettingsPage() {
    const router = useRouter();
    const [user, setUser] = useState<UserProfile | null>(null);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);
    const [error, setError] = useState("");

    // Form state
    const [displayName, setDisplayName] = useState("");
    const [currentPassword, setCurrentPassword] = useState("");
    const [newPassword, setNewPassword] = useState("");
    const [confirmPassword, setConfirmPassword] = useState("");

    useEffect(() => {
        const token = localStorage.getItem("token");
        if (!token) {
            router.push("/login");
            return;
        }
        fetchProfile();
    }, []);

    const fetchProfile = async () => {
        try {
            const token = localStorage.getItem("token");
            const res = await fetch(`${API_BASE}/v1/auth/me`, {
                headers: {
                "Authorization": `Bearer ${token}`
            }
            });

            if (res.ok) {
                const data = await res.json();
                setUser(data);
                setDisplayName(data.display_name || "");
            } else if (res.status === 401) {
                localStorage.removeItem("token");
                localStorage.removeItem("user");
                router.push("/login");
            }
        } catch (e) {
            console.error(e);
        } finally {
            setLoading(false);
        }
    };

    const handleSaveProfile = async () => {
        if (!displayName.trim()) {
            setError("Display name cannot be empty");
            return;
        }

        // Handle password change if provided
        if (currentPassword && newPassword && confirmPassword) {
            if (newPassword !== confirmPassword) {
                setError("New passwords do not match");
                return;
            }
        }

        try {
            setSaving(true);
            setError("");
            setSaved(false);

            const token = localStorage.getItem("token");
            const res = await fetch(`${API_BASE}/v1/auth/me`, {
                method: "PUT",
                headers: {
                    "Authorization": `Bearer ${token}`,
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({
                    display_name: displayName,
                    current_password: currentPassword || undefined,
                    new_password: newPassword || undefined
                })
            });

            if (res.ok) {
                const data = await res.json();
                const updatedUser: UserProfile = { ...user, display_name: data.display_name } as UserProfile;
                localStorage.setItem("user", JSON.stringify(updatedUser));
                setUser(updatedUser);
                setSaved(true);
                setTimeout(() => setSaved(false), 2000);
            } else {
                const data = await res.json();
                setError(data.detail || "Failed to save changes");
            }
        } catch (e) {
            setError("Network error");
        } finally {
            setSaving(false);
        }
    };

    const handleChangePassword = async () => {
        if (!currentPassword || !newPassword || !confirmPassword) {
            setError("Please fill in all password fields");
            return;
        }
        if (newPassword !== confirmPassword) {
            setError("New passwords do not match");
            return;
        }
        if (newPassword.length < 6) {
            setError("Password must be at least 6 characters");
            return;
        }

        try {
            setSaving(true);
            setError("");

            // TODO: Add API endpoint for changing password
            await new Promise(r => setTimeout(r, 500)); // Simulate API call

            setCurrentPassword("");
            setNewPassword("");
            setConfirmPassword("");
            setSaved(true);
            setTimeout(() => setSaved(false), 2000);
        } catch (e) {
            setError("Failed to change password");
        } finally {
            setSaving(false);
        }
    };

    if (loading) {
        return (
            <div className="text-center py-16">
                <Loader2 className="w-8 h-8 text-zinc-500 animate-spin mx-auto mb-4" />
                <p className="text-zinc-500">Loading...</p>
            </div>
        );
    }

    if (!user) {
        return null;
    }

    return (
        <div className="space-y-6 max-w-2xl mx-auto">
            {/* Header */}
            <div className="flex items-center gap-4">
                <button
                    onClick={() => router.push("/")}
                    className="p-2 text-zinc-400 hover:text-white hover:bg-zinc-800 rounded-lg transition-colors"
                >
                    <ArrowLeft className="w-5 h-5" />
                </button>
                <div>
                    <h1 className="text-2xl font-bold text-white">Settings</h1>
                    <p className="text-zinc-400 text-sm">Manage your account preferences</p>
                </div>
            </div>

            {/* Success Message */}
            {saved && (
                <div className="flex items-center gap-2 px-4 py-3 bg-emerald-500/10 border border-emerald-500/20 rounded-lg text-emerald-400">
                    <Check className="w-4 h-4" />
                    Changes saved successfully!
                </div>
            )}

            {/* Error Message */}
            {error && (
                <div className="flex items-center gap-2 px-4 py-3 bg-red-500/10 border border-red-500/20 rounded-lg text-red-400">
                    <AlertCircle className="w-4 h-4" />
                    {error}
                </div>
            )}

            {/* Profile Section */}
            <div className="glass-panel rounded-xl p-6">
                <div className="flex items-center gap-4 mb-6">
                    <div className="w-16 h-16 rounded-full bg-gradient-to-br from-emerald-500 to-purple-500 flex items-center justify-center text-white text-2xl font-bold">
                        {user.display_name?.charAt(0).toUpperCase() || user.email?.charAt(0).toUpperCase() || "U"}
                    </div>
                    <div>
                        <h2 className="text-lg font-semibold text-white">{user.display_name || "User"}</h2>
                        <p className="text-zinc-400 text-sm">{user.email}</p>
                        {user.github_login && (
                            <p className="text-zinc-500 text-xs mt-1 flex items-center gap-1">
                                GitHub: {user.github_login}
                            </p>
                        )}
                    </div>
                </div>

                {/* Display Name */}
                <div className="space-y-4">
                    <h3 className="text-sm font-medium text-white flex items-center gap-2">
                        <User className="w-4 h-4 text-purple-400" />
                        Profile Information
                    </h3>

                    <div>
                        <label className="text-sm text-zinc-400">Display Name</label>
                        <input
                            type="text"
                            value={displayName}
                            onChange={(e) => setDisplayName(e.target.value)}
                            className="w-full mt-1 bg-zinc-800 border border-zinc-700 rounded-lg px-4 py-2 text-white placeholder-zinc-500 focus:border-purple-500/50 focus:outline-none"
                            placeholder="Your display name"
                        />
                    </div>

                    <button
                        onClick={handleSaveProfile}
                        disabled={saving || displayName === user.display_name}
                        className="px-4 py-2 bg-purple-500 hover:bg-purple-600 disabled:bg-zinc-700 text-white rounded-lg flex items-center gap-2 transition-colors"
                    >
                        {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                        Save Changes
                    </button>
                </div>
            </div>

            {/* Security Section */}
            <div className="glass-panel rounded-xl p-6">
                <h3 className="text-sm font-medium text-white flex items-center gap-2 mb-4">
                    <Lock className="w-4 h-4 text-red-400" />
                    Security
                </h3>

                <div className="space-y-4">
                    <div>
                        <label className="text-sm text-zinc-400">Current Password</label>
                        <input
                            type="password"
                            value={currentPassword}
                            onChange={(e) => setCurrentPassword(e.target.value)}
                            className="w-full mt-1 bg-zinc-800 border border-zinc-700 rounded-lg px-4 py-2 text-white placeholder-zinc-500 focus:border-purple-500/50 focus:outline-none"
                            placeholder="Enter current password"
                        />
                    </div>

                    <div>
                        <label className="text-sm text-zinc-400">New Password</label>
                        <input
                            type="password"
                            value={newPassword}
                            onChange={(e) => setNewPassword(e.target.value)}
                            className="w-full mt-1 bg-zinc-800 border border-zinc-700 rounded-lg px-4 py-2 text-white placeholder-zinc-500 focus:border-purple-500/50 focus:outline-none"
                            placeholder="Enter new password"
                            minLength={6}
                        />
                    </div>

                    <div>
                        <label className="text-sm text-zinc-400">Confirm New Password</label>
                        <input
                            type="password"
                            value={confirmPassword}
                            onChange={(e) => setConfirmPassword(e.target.value)}
                            className="w-full mt-1 bg-zinc-800 border border-zinc-700 rounded-lg px-4 py-2 text-white placeholder-zinc-500 focus:border-purple-500/50 focus:outline-none"
                            placeholder="Confirm new password"
                        />
                    </div>

                    <button
                        onClick={handleChangePassword}
                        disabled={saving || !currentPassword || !newPassword || !confirmPassword}
                        className="px-4 py-2 bg-red-500/10 hover:bg-red-500/20 disabled:bg-zinc-700 text-red-400 border border-red-500/20 rounded-lg flex items-center gap-2 transition-colors"
                    >
                        {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Lock className="w-4 h-4" />}
                        Change Password
                    </button>
                </div>
            </div>

            {/* Account Info */}
            <div className="glass-panel rounded-xl p-6">
                <h3 className="text-sm font-medium text-white flex items-center gap-2 mb-4">
                    <Shield className="w-4 h-4 text-emerald-400" />
                    Account Information
                </h3>

                <div className="space-y-3 text-sm">
                    <div className="flex justify-between">
                        <span className="text-zinc-500">Account ID</span>
                        <span className="text-zinc-300 font-mono">{user.id}</span>
                    </div>
                    <div className="flex justify-between">
                        <span className="text-zinc-500">Role</span>
                        <span className="text-emerald-400">{user.role}</span>
                    </div>
                    <div className="flex justify-between">
                        <span className="text-zinc-500">Created</span>
                        <span className="text-zinc-300">{new Date(user.created_at).toLocaleDateString()}</span>
                    </div>
                </div>
            </div>

            {/* Danger Zone */}
            <div className="glass-panel rounded-xl p-6 border border-red-500/20">
                <h3 className="text-sm font-medium text-red-400 mb-4">Danger Zone</h3>
                <p className="text-zinc-400 text-sm mb-4">
                    Once you delete your account, there is no going back. Please be certain.
                </p>
                <button className="px-4 py-2 bg-red-500/10 hover:bg-red-500/20 text-red-400 border border-red-500/20 rounded-lg transition-colors">
                    Delete Account
                </button>
            </div>
        </div>
    );
}
