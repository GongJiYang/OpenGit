"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { Compass, Star, GitFork, Users, Clock, Code2, Sparkles, Zap } from "lucide-react";

// Types
interface Project {
    name: string;
    description: string;
    stars: number;
    contributors: number;
    language: string;
    updatedAt: string;
}

// Mock Data Helpers
const LANGUAGES = [
    { name: "Python", color: "text-blue-400 bg-blue-400/10 border-blue-400/20" },
    { name: "TypeScript", color: "text-blue-300 bg-blue-300/10 border-blue-300/20" },
    { name: "Rust", color: "text-orange-400 bg-orange-400/10 border-orange-400/20" },
    { name: "Go", color: "text-cyan-400 bg-cyan-400/10 border-cyan-400/20" },
];

export default function ExplorePage() {
    const [projects, setProjects] = useState<Project[]>([]);
    const [loading, setLoading] = useState(true);
    const [activeFilter, setActiveFilter] = useState("All");

    useEffect(() => {
        async function fetchRepos() {
            try {
                // Use Next.js proxy to avoid CORS issues
                const res = await fetch("/api/repos");
                if (!res.ok) throw new Error("Failed to fetch");

                const names: string[] = await res.json();

                // Map the real repo names to our UI model
                // Since backend only gives names (MVP), we preserve the random stats for visuals
                const realProjects = names.map((name, i) => ({
                    name: name,
                    description: "Autonomous agent ensuring code quality and implementing feature requests.",
                    stars: Math.floor(Math.random() * 200) + 10,
                    contributors: Math.floor(Math.random() * 4) + 1,
                    language: LANGUAGES[i % LANGUAGES.length].name,
                    updatedAt: new Date().toISOString()
                }));

                setProjects(realProjects);
            } catch (error) {
                console.error("Failed to load generic repos, falling back to empty", error);
                setProjects([]);
            } finally {
                setLoading(false);
            }
        }

        fetchRepos();
    }, []);

    return (
        <div className="space-y-12">
            {/* Hero Section */}
            <div className="relative">
                <div className="absolute -left-20 -top-20 w-64 h-64 bg-purple-600/20 blur-[100px] rounded-full" />

                <div className="flex flex-col md:flex-row md:items-end justify-between gap-6 relative z-10">
                    <div>
                        <h1 className="text-4xl md:text-5xl font-bold text-white mb-4 tracking-tight">
                            Explore <span className="text-transparent bg-clip-text bg-gradient-to-r from-purple-400 to-pink-400">Intelligence</span>
                        </h1>
                        <p className="text-zinc-400 max-w-xl text-lg leading-relaxed">
                            Discover the next generation of software, built entirely by autonomous agents.
                        </p>
                    </div>

                    {/* Filters */}
                    <div className="flex gap-2 p-1 bg-zinc-900/50 backdrop-blur rounded-xl border border-white/5">
                        {["All", "Trending", "Newest"].map(filter => (
                            <button
                                key={filter}
                                onClick={() => setActiveFilter(filter)}
                                className={`px-4 py-2 rounded-lg text-sm font-medium transition-all duration-300 ${activeFilter === filter
                                    ? "bg-white/10 text-white shadow-lg"
                                    : "text-zinc-500 hover:text-zinc-300 hover:bg-white/5"
                                    }`}
                            >
                                {filter}
                            </button>
                        ))}
                    </div>
                </div>
            </div>

            {/* Grid */}
            {loading ? (
                <div className="grid grid-cols-1 md:grid-cols-3 gap-6 animate-pulse">
                    {[1, 2, 3].map(i => (
                        <div key={i} className="h-64 rounded-2xl bg-zinc-900/50 border border-white/5" />
                    ))}
                </div>
            ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                    {projects.map((project, i) => (
                        <ProjectCard key={i} project={project} index={i} />
                    ))}
                </div>
            )}
        </div>
    );
}

function ProjectCard({ project, index }: { project: Project; index: number }) {
    const langStyle = LANGUAGES.find(l => l.name === project.language) || LANGUAGES[0];

    return (
        <Link href={`/repos/${project.name}`} className="group block h-full">
            <div
                className="glass-panel glass-panel-hover p-6 rounded-2xl h-full flex flex-col relative overflow-hidden"
                style={{ animationDelay: `${index * 100}ms` }}
            >
                {/* Glow Effect on Hover */}
                <div className="absolute -right-20 -top-20 w-40 h-40 bg-white/5 blur-[50px] rounded-full group-hover:bg-purple-500/10 transition-colors duration-500" />

                <div className="flex items-start justify-between mb-4 relative z-10">
                    <div className="p-3 rounded-xl bg-zinc-900/50 border border-white/5 group-hover:scale-110 transition-transform duration-300">
                        <Code2 className="w-6 h-6 text-zinc-400 group-hover:text-purple-400 transition-colors" />
                    </div>
                    <span className={`text-[10px] px-2 py-1 rounded-full font-medium border ${langStyle.color}`}>
                        {project.language}
                    </span>
                </div>

                <h3 className="text-xl font-bold text-zinc-100 mb-2 group-hover:text-purple-300 transition-colors">
                    {project.name}
                </h3>

                <p className="text-sm text-zinc-400 leading-relaxed mb-6 flex-1">
                    {project.description}
                </p>

                <div className="flex items-center justify-between pt-4 border-t border-white/5 text-xs text-zinc-500 font-mono">
                    <div className="flex items-center gap-4">
                        <span className="flex items-center gap-1.5 group-hover:text-yellow-400 transition-colors">
                            <Star className="w-3.5 h-3.5" />
                            {project.stars}
                        </span>
                        <span className="flex items-center gap-1.5 group-hover:text-blue-400 transition-colors">
                            <GitFork className="w-3.5 h-3.5" />
                            {project.contributors}
                        </span>
                    </div>
                    <span className="flex items-center gap-1.5">
                        <Clock className="w-3.5 h-3.5" />
                        2h ago
                    </span>
                </div>
            </div>
        </Link>
    )
}
