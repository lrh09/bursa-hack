import { redirect } from "next/navigation";

import { SITE } from "@/lib/site";

export default function CompareIndex() {
  redirect(`/compare/${SITE.defaultStrategy}/${SITE.alternateStrategy}/`);
}
