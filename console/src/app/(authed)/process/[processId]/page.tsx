import ProcessPage from "../page";

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
  return ProcessPage({
    searchParams: Promise.resolve({
      ...incoming,
      process: processId,
    }),
  });
}
