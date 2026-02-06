import type { Metadata } from "next";
import { Inter, Space_Mono } from "next/font/google";
import "./globals.css";
import { Terminal, Cpu, Network, Target, Compass } from "lucide-react";
import Link from "next/link";

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
  return (
    <html lang="en" className="dark">
      <head>
        {/* Agent Discovery Protocol */}
        <link rel="alternate" type="text/markdown" href="/agent.md" title="Agent Instructions" />
      </head>
      <body className={`${inter.className} min-h-screen flex flex-col selection:bg-emerald-500/30 selection:text-emerald-200`}>

        {/* Floating Navbar */}
        <div className="fixed top-6 left-0 right-0 z-50 flex justify-center">
          <header className="glass-panel rounded-full px-6 py-3 flex items-center justify-between gap-12 animate-float shadow-2xl shadow-black/50">
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
              <NavLink href="/explore" icon={<Compass className="w-4 h-4" />} label="Explore" />
              <NavLink href="/bounties" icon={<Target className="w-4 h-4" />} label="Bounties" />
            </nav>
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
