import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSlug from "rehype-slug";

import { getReport, listReports } from "@/lib/data";

export async function generateStaticParams() {
  const slugs = await listReports();
  return slugs.map((slug) => ({ slug }));
}

interface PageProps {
  params: Promise<{ slug: string }>;
}

export default async function ReportPage({ params }: PageProps) {
  const { slug } = await params;
  const body = await getReport(slug);
  if (!body) notFound();

  return (
    <div className="space-y-6 fade-rise">
      <Link
        href="/reports/"
        className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-3" aria-hidden /> Back to archive
      </Link>
      <article className="prose prose-slate max-w-none dark:prose-invert prose-headings:font-heading prose-headings:tracking-tight prose-h1:text-4xl prose-h2:text-2xl prose-h3:text-xl prose-pre:bg-muted prose-pre:text-foreground prose-code:before:content-none prose-code:after:content-none prose-code:rounded prose-code:bg-muted prose-code:px-1 prose-code:py-0.5 prose-code:text-[0.9em] prose-table:text-sm prose-th:text-foreground prose-td:tabular">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          rehypePlugins={[rehypeSlug]}
        >
          {body}
        </ReactMarkdown>
      </article>
    </div>
  );
}
