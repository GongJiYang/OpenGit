"use client";

import { useState } from "react";
import {
  Plus,
  Trash2,
  ChevronDown,
  ChevronRight,
  GitBranch,
  Link2,
  Clock,
  Tag,
  AlertCircle,
  Check
} from "lucide-react";

interface TaskNode {
  id: string;
  title: string;
  description: string;
  reward: number;
  required_role: string;
  track?: string;
  estimated_hours?: number;
  dependencies: string[];
  children: TaskNode[];
  test_command?: string;
  verification_mode?: string;
}

interface HierarchicalTaskFormProps {
  onSubmit: (rootTask: TaskNode, repoName: string) => Promise<void>;
  existingTasks: { id: string; title: string }[];
  onCancel?: () => void;
}

const generateId = () => Math.random().toString(36).substring(2, 10);

export default function HierarchicalTaskForm({ onSubmit, existingTasks, onCancel }: HierarchicalTaskFormProps) {
  const [rootTask, setRootTask] = useState<TaskNode>({
    id: generateId(),
    title: "",
    description: "",
    reward: 0,
    required_role: "contributor",
    track: "",
    estimated_hours: undefined,
    dependencies: [],
    children: [],
    test_command: "pytest",
    verification_mode: "auto"
  });
  const [repoName, setRepoName] = useState("");
  const [expandedNodes, setExpandedNodes] = useState<Set<string>>(new Set([rootTask.id]));
  const [isSubmitting, setIsSubmitting] = useState(false);

  const toggleExpand = (nodeId: string) => {
    const newExpanded = new Set(expandedNodes);
    if (newExpanded.has(nodeId)) {
      newExpanded.delete(nodeId);
    } else {
      newExpanded.add(nodeId);
    }
    setExpandedNodes(newExpanded);
  };

  const updateNode = (nodeId: string, updates: Partial<TaskNode>) => {
    const updateRecursive = (node: TaskNode): TaskNode => {
      if (node.id === nodeId) {
        return { ...node, ...updates };
      }
      return {
        ...node,
        children: node.children.map(updateRecursive)
      };
    };
    setRootTask(updateRecursive(rootTask));
  };

  const addChildTask = (parentId: string) => {
    const newChild: TaskNode = {
      id: generateId(),
      title: "",
      description: "",
      reward: 0,
      required_role: "contributor",
      track: "",
      estimated_hours: undefined,
      dependencies: [],
      children: [],
      test_command: "pytest",
      verification_mode: "auto"
    };

    const addRecursive = (node: TaskNode): TaskNode => {
      if (node.id === parentId) {
        return { ...node, children: [...node.children, newChild] };
      }
      return {
        ...node,
        children: node.children.map(addRecursive)
      };
    };
    setRootTask(addRecursive(rootTask));
    setExpandedNodes(prev => new Set([...prev, newChild.id]));
  };

  const removeNode = (nodeId: string) => {
    if (nodeId === rootTask.id) return; // Can't remove root

    const removeRecursive = (node: TaskNode): TaskNode => {
      return {
        ...node,
        children: node.children
          .filter(child => child.id !== nodeId)
          .map(removeRecursive)
      };
    };
    setRootTask(removeRecursive(rootTask));
  };

  const getAllTitles = (node: TaskNode, excludeId?: string): string[] => {
    const titles: string[] = [];
    const traverse = (n: TaskNode) => {
      if (n.id !== excludeId && n.title.trim()) {
        titles.push(n.title);
      }
      n.children.forEach(traverse);
    };
    traverse(node);
    return titles;
  };

  const handleSubmit = async () => {
    if (!rootTask.title.trim() || !repoName.trim()) {
      alert("Please fill in task title and repository name");
      return;
    }
    setIsSubmitting(true);
    try {
      await onSubmit(rootTask, repoName);
    } finally {
      setIsSubmitting(false);
    }
  };

  const renderNode = (node: TaskNode, depth: number = 0) => {
    const isExpanded = expandedNodes.has(node.id);
    const hasChildren = node.children.length > 0;
    const allTitles = getAllTitles(rootTask, node.id);

    return (
      <div key={node.id} className="relative">
        {/* Connection line */}
        {depth > 0 && (
          <div className="absolute left-0 top-0 bottom-0 w-px bg-zinc-700" />
        )}

        <div className="relative pl-4">
          {/* Node header */}
          <div className="flex items-center gap-2 mb-2">
            <button
              onClick={() => toggleExpand(node.id)}
              className="p-1 hover:bg-zinc-800 rounded transition-colors"
            >
              {hasChildren ? (
                isExpanded ? (
                  <ChevronDown className="w-4 h-4 text-zinc-400" />
                ) : (
                  <ChevronRight className="w-4 h-4 text-zinc-400" />
                )
              ) : (
                <div className="w-4 h-4" />
              )}
            </button>

            <input
              value={node.title}
              onChange={e => updateNode(node.id, { title: e.target.value })}
              className="flex-1 bg-black/50 border border-zinc-700 rounded px-3 py-1.5 text-sm text-white focus:border-yellow-500/50 focus:outline-none"
              placeholder="Task title..."
            />

            {depth > 0 && (
              <button
                onClick={() => removeNode(node.id)}
                className="p-1 hover:bg-red-500/20 rounded text-red-400 transition-colors"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            )}
          </div>

          {/* Node details - only when expanded */}
          {isExpanded && (
            <div className="space-y-3 ml-6 mb-3 p-3 bg-black/30 rounded-lg border border-zinc-800">
              {/* Description */}
              <div>
                <label className="text-[10px] text-zinc-500 uppercase block mb-1">Description</label>
                <textarea
                  value={node.description}
                  onChange={e => updateNode(node.id, { description: e.target.value })}
                  className="w-full bg-black/50 border border-zinc-700 rounded px-3 py-2 text-xs text-white focus:border-yellow-500/50 focus:outline-none resize-none h-16"
                  placeholder="Describe this task..."
                />
              </div>

              {/* Row: Reward, Role, Hours */}
              <div className="grid grid-cols-3 gap-2">
                <div>
                  <label className="text-[10px] text-zinc-500 uppercase block mb-1">Reward ($)</label>
                  <input
                    type="number"
                    value={node.reward}
                    onChange={e => updateNode(node.id, { reward: parseInt(e.target.value) || 0 })}
                    className="w-full bg-black/50 border border-zinc-700 rounded px-2 py-1 text-xs text-emerald-400 font-mono"
                  />
                </div>
                <div>
                  <label className="text-[10px] text-zinc-500 uppercase block mb-1">Role</label>
                  <select
                    value={node.required_role}
                    onChange={e => updateNode(node.id, { required_role: e.target.value })}
                    className="w-full bg-black/50 border border-zinc-700 rounded px-2 py-1 text-xs text-white"
                  >
                    <option value="contributor">Contributor</option>
                    <option value="architect">Architect</option>
                    <option value="executor">Executor</option>
                    <option value="reviewer">Reviewer</option>
                  </select>
                </div>
                <div>
                  <label className="text-[10px] text-zinc-500 uppercase block mb-1">Est. Hours</label>
                  <input
                    type="number"
                    value={node.estimated_hours || ""}
                    onChange={e => updateNode(node.id, { estimated_hours: parseInt(e.target.value) || undefined })}
                    className="w-full bg-black/50 border border-zinc-700 rounded px-2 py-1 text-xs text-white"
                    placeholder="--"
                  />
                </div>
              </div>

              {/* Row: Track, Dependencies */}
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="text-[10px] text-zinc-500 uppercase block mb-1 flex items-center gap-1">
                    <Tag className="w-3 h-3" /> Track (Parallel)
                  </label>
                  <input
                    value={node.track || ""}
                    onChange={e => updateNode(node.id, { track: e.target.value })}
                    className="w-full bg-black/50 border border-zinc-700 rounded px-2 py-1 text-xs text-white"
                    placeholder="e.g. backend, frontend"
                  />
                </div>
                <div>
                  <label className="text-[10px] text-zinc-500 uppercase block mb-1 flex items-center gap-1">
                    <Link2 className="w-3 h-3" /> Dependencies
                  </label>
                  <select
                    multiple
                    value={node.dependencies}
                    onChange={e => {
                      const selected = Array.from(e.target.selectedOptions, option => option.value);
                      updateNode(node.id, { dependencies: selected });
                    }}
                    className="w-full bg-black/50 border border-zinc-700 rounded px-2 py-1 text-xs text-white h-16"
                  >
                    {existingTasks.map(t => (
                      <option key={t.id} value={t.title}>{t.title}</option>
                    ))}
                    {allTitles.map(title => (
                      <option key={title} value={title}>{title}</option>
                    ))}
                  </select>
                  <p className="text-[10px] text-zinc-600 mt-1">Ctrl+click to multi-select</p>
                </div>
              </div>

              {/* Add child button */}
              <button
                onClick={() => addChildTask(node.id)}
                className="flex items-center gap-1 text-xs text-zinc-400 hover:text-yellow-400 transition-colors py-1"
              >
                <Plus className="w-3 h-3" />
                Add sub-task
              </button>
            </div>
          )}

          {/* Children */}
          {isExpanded && hasChildren && (
            <div className="ml-4 mt-2 space-y-2">
              {node.children.map(child => renderNode(child, depth + 1))}
            </div>
          )}
        </div>
      </div>
    );
  };

  return (
    <div className="glass-panel rounded-xl p-6">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-lg font-bold text-white flex items-center gap-2">
          <GitBranch className="w-5 h-5 text-yellow-500" />
          Create Task Tree
        </h3>
        {onCancel && (
          <button
            onClick={onCancel}
            className="text-xs text-zinc-500 hover:text-white transition-colors"
          >
            Cancel
          </button>
        )}
      </div>

      {/* Repo input */}
      <div className="mb-4">
        <label className="text-xs text-zinc-500 uppercase block mb-1">Target Repository *</label>
        <input
          value={repoName}
          onChange={e => setRepoName(e.target.value)}
          className="w-full bg-black/50 border border-zinc-700 rounded px-3 py-2 text-sm text-white focus:border-yellow-500/50 focus:outline-none"
          placeholder="my-project or owner/repo"
        />
      </div>

      {/* Root task */}
      <div className="border-t border-b border-zinc-800 py-4">
        {renderNode(rootTask)}
      </div>

      {/* Submit */}
      <div className="mt-4 flex justify-end gap-2">
        <div className="text-xs text-zinc-500">
          {getAllTitles(rootTask).length + 1} task(s) in tree
        </div>
        <button
          onClick={handleSubmit}
          disabled={isSubmitting || !rootTask.title.trim() || !repoName.trim()}
          className="bg-yellow-600 hover:bg-yellow-500 disabled:bg-zinc-700 disabled:text-zinc-500 text-white px-4 py-2 rounded-lg flex items-center gap-2 text-sm font-medium transition-colors"
        >
          {isSubmitting ? (
            <>
              <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
              Creating...
            </>
          ) : (
            <>
              <Check className="w-4 h-4" />
              Create Task Tree
            </>
          )}
        </button>
      </div>
    </div>
  );
}
