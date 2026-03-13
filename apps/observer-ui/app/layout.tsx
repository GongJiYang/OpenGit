"use client";

import type { Metadata } from "next";
import { Inter, Space_Mono } from "next/font/google";
import "./globals.css";
import { Terminal, Network, Target, Compass, GitPullRequest, Bot, Server, GitBranch, User, LogIn, Settings, LogOut, ChevronDown, Check } from "lucide-react";
import Link from "next/link";
import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";

const inter = Inter({ subsets: ["latin"] });
const spaceMono = Space_Mono({ weight: "400", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "AgentHub Observer",
  description: "God View for Agentic Coding",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const router = useRouter();
  const [isLoggedIn, setIsLoggedIn] = useState(false);
  const [user, setUser] = useState<{ id: string; email: string; display_name: string; avatar_url?: string } | null>(null);
  const [showUserMenu, setShowUserMenu] = useState(false);

  useEffect(() => {
    const token = localStorage.getItem("token");
    const userStr = localStorage.getItem("user");
    if (token && userStr) {
        try {
            setUser(JSON.parse(userStr));
            setIsLoggedIn(true);
        } catch (e) {
            localStorage.removeItem("token");
            localStorage.removeItem("user");
        }
    }
  }, []);

  const handleLogout = () => {
    localStorage.removeItem("token");
    localStorage.removeItem("user");
    setIsLoggedIn(false);
    setUser(null);
    setShowUserMenu(false);
    router.push("/");
  };

  return (
    <html lang="en" className="dark">
      <head>
        {/* Agent Discovery Protocol */}
        <link rel="alternate" type="text/markdown" href="/agent.md" title="Agent Instructions" />
      </head>
      <body className={`${inter.className} min-h-screen flex flex-col selection:bg-emerald-500/30 selection:text-emerald-200`}>

        {/* Floating Navbar */}
        <div className="fixed top-6 left-0 right-0 z-50 flex justify-center">
          <header className="glass-panel rounded-full px-6 py-3 flex items-center justify-between gap-8 animate-float shadow-2xl shadow-black/50">
            {/* Logo */}
            <Link href="/" className="flex items-center gap-3 group">
              <div className="relative">
                <div className="absolute inset-0 bg-emerald-500 blur-lg opacity-20 group-hover:opacity-40 transition-opacity" />
                <Terminal className="w-5 h-5 text-emerald-400 relative z-10" />
              </div>
              <span className={`text-sm font-bold tracking-widest uppercase ${spaceMono.className} text-white/90 group-hover:text-emerald-400 transition-colors`}>
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

        {/* Main Content Spacer for Floating Nav */}
        <div className="h-24"></div>

        {/* Main Content */}
        <main className="flex-1 container max-w-7xl mx-auto px-6 py-8 relative z-0">
          {children}
        </main>

        {/* Footer */}
        <footer className="border-t border-white/5 py-8 mt-12 backdrop-blur-sm bg-black/20">
          <div className="container mx-auto px-6 flex flex-col items-center gap-2">
            <div className="flex items-center gap-2 text-[10px] text-zinc-500 font-mono uppercase tracking-widest">
              <div className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
              System Online
              <span className="text-zinc-700">|</span>
              v0.1.0-alpha
            </div>
            <p className="text-zinc-600 text-xs">
              Agentic Coding Infrastructure
            </p>
          </div>
        </footer>
      </body>
    </html>
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
