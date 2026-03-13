import type { Metadata } from "next";
import { Inter, Space_Mono } from "next/font/google";
import "./globals.css";
import Navigation from "./components/Navigation";

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
        <Navigation />

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
