import { notFound, redirect } from "next/navigation";

import { getManifest } from "@/lib/data";

export async function generateStaticParams() {
  const manifest = await getManifest();
  // Prerender every known hash so the redirect resolves at build time.
  return Object.keys(manifest.hash_to_strategy_id ?? {}).map((hash) => ({ hash }));
}

interface PageProps {
  params: Promise<{ hash: string }>;
}

export default async function VariantRedirect({ params }: PageProps) {
  const { hash } = await params;
  const manifest = await getManifest();
  const sid = manifest.hash_to_strategy_id?.[hash];
  if (!sid) notFound();
  redirect(`/strategies/${sid}/?v=${hash}`);
}
