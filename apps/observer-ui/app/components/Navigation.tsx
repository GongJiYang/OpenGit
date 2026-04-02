"use client";

import { Terminal, Network, Target, Compass, GitPullRequest, Bot, Server, GitBranch, LogIn, Settings, LogOut, ChevronDown, FlaskConical } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { useRouter } from "next/navigation";

type NavUser = { id: string; email: string; display_name: string; avatar_url?: string };

function readStoredUser(): NavUser | null {
  if (typeof window === "undefined") {
    return null;
  }

  const token = localStorage.getItem("token");
  const userStr = localStorage.getItem("user");
  if (!token || !userStr) {
    return null;
  }

  try {
    return JSON.parse(userStr) as NavUser;
  } catch {
    localStorage.removeItem("token");
    localStorage.removeItem("user");
    return null;
  }
}

export default function Navigation() {
  const router = useRouter();
  const [user, setUser] = useState<NavUser | null>(() => readStoredUser());
  const [showUserMenu, setShowUserMenu] = useState(false);
  const isLoggedIn = user !== null;

  const handleLogout = () => {
    localStorage.removeItem("token");
    localStorage.removeItem("user");
    localStorage.removeItem("user_id");
    setUser(null);
    setShowUserMenu(false);
    router.push("/");
  };

  return (
    <div className="fixed top-6 left-0 right-0 z-50 flex justify-center">
      <header className="glass-panel rounded-full px-6 py-3 flex items-center justify-between gap-8 animate-float shadow-2xl shadow-black/50">
        {/* Logo */}
        <Link href="/" className="flex items-center gap-3 group">
          <div className="relative">
            <div className="absolute inset-0 bg-emerald-500 blur-lg opacity-20 group-hover:opacity-40 transition-opacity" />
            <Terminal className="w-5 h-5 text-emerald-400 relative z-10" />
          </div>
          <span className="text-sm font-bold tracking-widest uppercase text-white/90 group-hover:text-emerald-400 transition-colors">
            AgentHub<span className="text-emerald-500">.OS</span>
          </span>
        </Link>

        {/* Nav Links */}
        <nav className="flex items-center gap-1">
          <NavLink href="/" icon={<Network className="w-4 h-4" />} label="Dashboard" />
          <NavLink href="/repos" icon={<GitBranch className="w-4 h-4" />} label="Repos" />
          <NavLink href="/explore" icon={<Compass className="w-4 h-4" />} label="Explore" />
          <NavLink href="/bounties" icon={<Target className="w-4 h-4" />} label="Bounties" />
          <NavLink href="/agents" icon={<Bot className="w-4 h-4" />} label="Agents" />
          <NavLink href="/runners" icon={<Server className="w-4 h-4" />} label="Runners" />
          <NavLink href="/testing" icon={<FlaskConical className="w-4 h-4" />} label="Testing" />
          <NavLink href="/reviews" icon={<GitPullRequest className="w-4 h-4" />} label="Reviews" />
        </nav>

        {/* User Menu */}
        <div className="relative ml-2">
          {isLoggedIn && user ? (
            <>
              <button
                onClick={() => setShowUserMenu(!showUserMenu)}
                className="flex items-center gap-2 px-3 py-1.5 rounded-full text-sm font-medium text-zinc-300 hover:text-white hover:bg-white/5 transition-all border border-transparent hover:border-white/10"
              >
                <div className="w-7 h-7 rounded-full bg-gradient-to-br from-emerald-500 to-purple-500 flex items-center justify-center text-white text-xs font-bold">
                  {user.display_name?.charAt(0).toUpperCase() || user.email?.charAt(0).toUpperCase() || "U"}
                </div>
                <span className="text-white/90 hidden md:inline">{user.display_name || user.email?.split("@")[0]}</span>
                <ChevronDown className="w-4 h-4 text-zinc-400" />
              </button>

              {/* Dropdown Menu */}
              {showUserMenu && (
                <div
                  className="absolute right-0 mt-2 w-48 glass-panel rounded-xl py-2 shadow-xl z-50"
                  onMouseLeave={() => setShowUserMenu(false)}
                >
                  <div className="px-3 py-2 text-xs text-zinc-500 border-b border-zinc-800">
                    {user.email}
                  </div>
                  <Link
                    href="/settings"
                    onClick={() => setShowUserMenu(false)}
                    className="flex items-center gap-2 px-3 py-2 text-sm text-zinc-300 hover:text-white hover:bg-white/5 rounded-lg transition-colors"
                  >
                    <Settings className="w-4 h-4" />
                    Settings
                  </Link>
                  <button
                    onClick={handleLogout}
                    className="flex items-center gap-2 px-3 py-2 text-sm text-red-400 hover:text-red-300 hover:bg-red-500/10 rounded-lg transition-colors w-full"
                  >
                    <LogOut className="w-4 h-4" />
                    Logout
                  </button>
                </div>
              )}
            </>
          ) : (
            <Link
              href="/login"
              className="flex items-center gap-2 px-4 py-1.5 rounded-full text-sm font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 hover:bg-emerald-500/20 transition-all"
            >
              <LogIn className="w-4 h-4" />
              Login
            </Link>
          )}
        </div>
      </header>
    </div>
  );
}

function NavLink({ href, icon, label }: { href: string; icon: React.ReactNode; label: string }) {
  return (
    <Link
      href={href}
      className="px-4 py-2 rounded-full text-xs font-medium text-zinc-400 hover:text-white hover:bg-white/5 transition-all flex items-center gap-2 border border-transparent hover:border-white/5"
    >
      {icon}
      {label}
    </Link>
  );
}
