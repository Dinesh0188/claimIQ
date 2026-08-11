"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import {
  FileCheck,
  LayoutDashboard,
  List,
  BookOpen,
  Activity,
  Menu,
  X,
} from "lucide-react";
import { useState } from "react";
import { claimiqApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import { ProfileSelector } from "./profile-selector";
import { ConnectionGuard } from "./connection-guard";

const NAV_ITEMS = [
  { path: "/", label: "Check a claim", icon: FileCheck },
  { path: "/claims", label: "Claims", icon: List },
  { path: "/dashboard", label: "Leakage", icon: LayoutDashboard },
  { path: "/rules", label: "Rule catalog", icon: BookOpen },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const { data: health } = useQuery({
    queryKey: ["health"],
    queryFn: claimiqApi.health,
    refetchInterval: 30_000,
  });

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Mobile overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-black/60 z-40 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar */}
      <aside
        className={cn(
          "fixed lg:static inset-y-0 left-0 z-50 w-[232px] bg-panel-2 border-r border-line flex flex-col transition-transform lg:translate-x-0",
          sidebarOpen ? "translate-x-0" : "-translate-x-full"
        )}
      >
        {/* Brand */}
        <div className="px-5 py-5 border-b border-line">
          <Link href="/" className="flex items-center gap-2">
            <span className="text-lg font-bold text-white">ClaimIQ</span>
            <span className="text-xs text-muted">Pre-submission audit</span>
          </Link>
        </div>

        {/* Navigation */}
        <nav className="flex-1 px-3 py-4 space-y-1" aria-label="Primary">
          {NAV_ITEMS.map((item) => {
            const isActive =
              item.path === "/"
                ? pathname === "/"
                : pathname.startsWith(item.path);
            return (
              <Link
                key={item.path}
                href={item.path}
                onClick={() => setSidebarOpen(false)}
                className={cn(
                  "flex items-center gap-3 px-3 py-2.5 rounded-md text-sm transition-colors",
                  isActive
                    ? "bg-accent/10 text-accent"
                    : "text-muted hover:text-white hover:bg-panel-3"
                )}
                aria-current={isActive ? "page" : undefined}
              >
                <item.icon size={18} />
                {item.label}
              </Link>
            );
          })}
        </nav>

        {/* Profile selector */}
        <div className="px-3 py-3 border-t border-line">
          <ProfileSelector />
        </div>

        {/* Status footer */}
        <div className="px-4 py-3 border-t border-line space-y-1">
          {health ? (
            <>
              <div className="flex items-center gap-2">
                <Activity size={12} className="text-settled" />
                <span
                  className={cn(
                    "text-xs px-1.5 py-0.5 rounded",
                    health.key_present && health.ai_enabled
                      ? "bg-settled/10 text-settled"
                      : "bg-line text-muted"
                  )}
                >
                  {health.key_present && health.ai_enabled
                    ? "AI reading on"
                    : "Rules only"}
                </span>
              </div>
              <div className="text-xs text-muted-2">{health.provider}</div>
              <div className="text-xs font-mono text-muted-2">
                {health.corpus_version}
              </div>
            </>
          ) : (
            <div className="text-xs text-muted-2">Connecting...</div>
          )}
        </div>
      </aside>

      {/* Main content */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Mobile header */}
        <header className="lg:hidden flex items-center justify-between px-4 py-3 bg-panel-2 border-b border-line">
          <button
            onClick={() => setSidebarOpen(true)}
            className="p-2 text-muted hover:text-white"
            aria-label="Open menu"
          >
            <Menu size={20} />
          </button>
          <span className="text-sm font-bold">ClaimIQ</span>
          <div className="w-9" />
        </header>

        {/* Page content */}
        <main className="flex-1 overflow-y-auto p-6">
          <ConnectionGuard>{children}</ConnectionGuard>
        </main>
      </div>
    </div>
  );
}
