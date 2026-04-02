"use client";

import { useState, useEffect, useCallback } from "react";
import {
  Globe,
  Copy,
  CheckCircle,
  AlertCircle,
  RefreshCw,
  ExternalLink,
  Activity,
  Loader2
} from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "/api";

interface ServiceEndpointInfo {
  job_id: string;
  service_endpoint: string | null;
  token_expires_at: string | null;
  status: string;
  passed: boolean | null;
  is_ready_for_testing: boolean;
}

interface ServiceEndpointPanelProps {
  jobId: string;
  onStatusChange?: (status: ServiceEndpointInfo) => void;
}

export default function ServiceEndpointPanel({ jobId, onStatusChange }: ServiceEndpointPanelProps) {
  const [serviceInfo, setServiceInfo] = useState<ServiceEndpointInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  const getAuthHeaders = (): Record<string, string> => {
    const token = localStorage.getItem("token");
    const headers: Record<string, string> = {};
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }
    return headers;
  };

  const fetchServiceStatus = useCallback(async () => {
    if (!jobId) return;

    setLoading(true);
    setError(null);

    try {
      const res = await fetch(`${API_BASE}/v1/runners/jobs/${jobId}/service-status`, {
        headers: getAuthHeaders(),
      });

      if (!res.ok) {
        if (res.status === 404) {
          setError("Job not found");
        } else {
          setError(`Failed to fetch status: ${res.status}`);
        }
        return;
      }

      const data = await res.json();
      setServiceInfo(data);
      onStatusChange?.(data);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to fetch service status");
    } finally {
      setLoading(false);
    }
  }, [jobId, onStatusChange]);

  // Initial fetch and polling
  useEffect(() => {
    fetchServiceStatus();

    // Poll every 10 seconds if service is not ready yet
    const interval = setInterval(() => {
      if (!serviceInfo?.is_ready_for_testing) {
        fetchServiceStatus();
      }
    }, 10000);

    return () => clearInterval(interval);
  }, [jobId, serviceInfo?.is_ready_for_testing, fetchServiceStatus]);


  const copyToClipboard = async (text: string, label: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(label);
      setTimeout(() => setCopied(null), 2000);
    } catch (e) {
      console.error("Failed to copy:", e);
    }
  };

  const getStatusColor = () => {
    if (!serviceInfo) return "text-zinc-400";
    switch (serviceInfo.status) {
      case "running":
        return "text-blue-400";
      case "completed":
        return serviceInfo.passed ? "text-emerald-400" : "text-red-400";
      case "failed":
      case "timeout":
        return "text-red-400";
      default:
        return "text-zinc-400";
    }
  };


  if (loading && !serviceInfo) {
    return (
      <div className="glass-panel rounded-xl p-6">
        <div className="flex items-center gap-2 text-zinc-400">
          <Loader2 className="w-4 h-4 animate-spin" />
          <span className="text-sm">Loading service status...</span>
        </div>
      </div>
    );
  }

  if (error && !serviceInfo) {
    return (
      <div className="glass-panel rounded-xl p-6 border border-red-500/30">
        <div className="flex items-center gap-2 text-red-400">
          <AlertCircle className="w-4 h-4" />
          <span className="text-sm">{error}</span>
        </div>
      </div>
    );
  }

  if (!serviceInfo) return null;

  return (
    <div className="glass-panel rounded-xl p-6 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-bold text-white flex items-center gap-2">
          <Globe className="w-5 h-5 text-purple-400" />
          Service Endpoint
        </h3>
        <button
          onClick={fetchServiceStatus}
          disabled={loading}
          className="flex items-center gap-1.5 px-2.5 py-1 text-xs rounded bg-zinc-800 text-zinc-300 hover:text-white transition-colors"
        >
          <RefreshCw className={`w-3 h-3 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </div>

      {/* Status Badge */}
      <div className="flex items-center gap-3">
        <div className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${getStatusColor()} bg-zinc-800`}>
          <Activity className="w-3 h-3" />
          {serviceInfo.status.replace("_", " ").toUpperCase()}
        </div>
        {serviceInfo.passed !== null && (
          <div className={`flex items-center gap-1 text-xs ${serviceInfo.passed ? "text-emerald-400" : "text-red-400"}`}>
            {serviceInfo.passed ? <CheckCircle className="w-3.5 h-3.5" /> : <AlertCircle className="w-3.5 h-3.5" />}
            Tests {serviceInfo.passed ? "Passed" : "Failed"}
          </div>
        )}
        {!serviceInfo.is_ready_for_testing && (
          <div className="flex items-center gap-1 text-xs text-orange-400">
            <Loader2 className="w-3 h-3 animate-spin" />
            Service deploying...
          </div>
        )}
      </div>

      {/* Service Endpoint */}
      {serviceInfo.service_endpoint ? (
        <div className="space-y-2">
          <label className="text-xs text-zinc-500 uppercase">Endpoint URL</label>
          <div className="flex items-center gap-2">
            <div className="flex-1 bg-black/50 border border-zinc-800 rounded px-3 py-2 text-sm text-zinc-200 font-mono truncate">
              {serviceInfo.service_endpoint}
            </div>
            <button
              onClick={() => copyToClipboard(serviceInfo.service_endpoint!, "endpoint")}
              className="p-2 rounded bg-zinc-800 text-zinc-400 hover:text-white transition-colors"
              title="Copy URL"
            >
              {copied === "endpoint" ? (
                <CheckCircle className="w-4 h-4 text-emerald-400" />
              ) : (
                <Copy className="w-4 h-4" />
              )}
            </button>
            <a
              href={serviceInfo.service_endpoint}
              target="_blank"
              rel="noopener noreferrer"
              className="p-2 rounded bg-zinc-800 text-zinc-400 hover:text-white transition-colors"
              title="Open in new tab"
            >
              <ExternalLink className="w-4 h-4" />
            </a>
          </div>
        </div>
      ) : (
        <div className="text-sm text-zinc-500 flex items-center gap-2">
          <AlertCircle className="w-4 h-4" />
          No service endpoint available yet
        </div>
      )}


      {/* Health Check URL */}
      {serviceInfo.service_endpoint && (
        <div className="pt-3 border-t border-zinc-800">
          <p className="text-xs text-zinc-500 mb-2">Quick Health Check:</p>
          <code className="text-[11px] text-zinc-400 font-mono bg-black/30 px-2 py-1 rounded block">
            {serviceInfo.service_endpoint}/health
          </code>
        </div>
      )}
    </div>
  );
}
