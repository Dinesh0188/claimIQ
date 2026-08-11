import { cn } from "@/lib/utils";

interface BadgeProps {
  variant?: "default" | "blocker" | "warning" | "info" | "success" | "muted";
  children: React.ReactNode;
  className?: string;
}

const variants: Record<string, string> = {
  default: "bg-accent/10 text-accent",
  blocker: "bg-red-500/10 text-red-400",
  warning: "bg-amber-500/10 text-amber-400",
  info: "bg-blue-500/10 text-blue-400",
  success: "bg-settled/10 text-settled",
  muted: "bg-line text-muted",
};

export function Badge({ variant = "default", children, className }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center px-2 py-0.5 text-xs font-medium rounded",
        variants[variant],
        className
      )}
    >
      {children}
    </span>
  );
}
