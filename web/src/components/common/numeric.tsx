import { cn } from "@/lib/utils";

export function Numeric({
  className,
  children,
  ...props
}: React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span className={cn("tabular", className)} {...props}>
      {children}
    </span>
  );
}
