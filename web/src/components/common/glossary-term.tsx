"use client";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { GLOSSARY } from "@/lib/glossary";
import { cn } from "@/lib/utils";

interface Props {
  term: keyof typeof GLOSSARY | string;
  children?: React.ReactNode;
  className?: string;
}

export function GlossaryTerm({ term, children, className }: Props) {
  const entry = (GLOSSARY as Record<string, { short: string; long: string }>)[term as string];
  if (!entry) {
    return <span className={className}>{children ?? term}</span>;
  }
  return (
    <Tooltip>
      <TooltipTrigger
        className={cn(
          "underline decoration-dotted decoration-foreground/40 underline-offset-2 cursor-help focus:outline-none focus-visible:ring-1 focus-visible:ring-ring rounded-sm",
          className,
        )}
      >
        <dfn>{children ?? term}</dfn>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs text-xs leading-relaxed">
        <p className="font-semibold mb-1">{entry.short}</p>
        <p>{entry.long}</p>
      </TooltipContent>
    </Tooltip>
  );
}
