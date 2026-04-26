import { redirect } from "next/navigation";

type SearchParams = { [key: string]: string | string[] | undefined };

export default async function ProcessByIdPage({
  params,
  searchParams,
}: {
  params: Promise<{ processId: string }>;
  searchParams?: Promise<SearchParams>;
}) {
  const { processId } = await params;
  const incoming = (await searchParams) ?? {};
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(incoming)) {
    if (Array.isArray(value)) {
      for (const item of value) query.append(key, item);
    } else if (value) {
      query.set(key, value);
    }
  }
  query.set("process", processId);
  redirect(`/process?${query.toString()}`);
}
