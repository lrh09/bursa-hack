import { NextResponse } from "next/server";
import { getAllVariants } from "@/lib/data";

export const dynamic = "force-static";
export const revalidate = false;

export async function GET() {
  const variants = await getAllVariants();
  // slim payload for the command palette
  return NextResponse.json(
    variants.map((v) => ({
      hash: v.params_hash,
      strategy: v.strategy,
      oosSharpe: v.oos_sharpe,
      tier: v.tier,
      sharpeMean: v.sharpe_mean,
      cagrMean: v.cagr_mean,
    })),
  );
}
