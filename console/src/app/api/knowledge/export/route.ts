import JSZip from "jszip";
import { NextRequest, NextResponse } from "next/server";

import {
  ApiHttpError,
  isApiConfigured,
  listBucketArticles,
  listBuckets,
  listWorkspaces,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  if (!isApiConfigured()) {
    return NextResponse.json(
      { error: "API is not configured" },
      { status: 503 },
    );
  }

  const token = await getSessionToken();
  const workspaceId = request.nextUrl.searchParams.get("workspaceId");
  if (!workspaceId) {
    return NextResponse.json({ error: "workspaceId is required" }, { status: 400 });
  }

  try {
    const [workspaces, buckets] = await Promise.all([
      listWorkspaces(token ?? undefined),
      listBuckets(workspaceId, { token: token ?? undefined }),
    ]);
    const workspace = workspaces.find((item) => item.id === workspaceId);
    const zip = new JSZip();
    zip.file(
      "workspace.json",
      JSON.stringify(
        {
          exported_at: new Date().toISOString(),
          workspace: workspace
            ? { id: workspace.id, slug: workspace.slug, name: workspace.name }
            : { id: workspaceId },
          bucket_count: buckets.length,
        },
        null,
        2,
      ),
    );

    await Promise.all(
      buckets.map(async (bucket) => {
        const folder = zip.folder(safePathPart(bucket.slug))!;
        folder.file(
          "_bucket.json",
          JSON.stringify(
            {
              id: bucket.id,
              slug: bucket.slug,
              name: bucket.name,
              description: bucket.description,
              scope_kind: bucket.scope_kind,
              source_kind: bucket.source_kind,
              source_ref: bucket.source_ref,
              archived_at: bucket.archived_at,
              created_at: bucket.created_at,
              updated_at: bucket.updated_at,
            },
            null,
            2,
          ),
        );

        const articles = await listBucketArticles(
          workspaceId,
          bucket.slug,
          {},
          token ?? undefined,
        );
        for (const article of articles) {
          folder.file(
            `${safePathPart(article.slug)}.md`,
            renderArticleMarkdown(bucket.name, article),
          );
        }
      }),
    );

    const bytes = await zip.generateAsync({ type: "arraybuffer" });
    const workspaceSlug = workspace?.slug ?? workspaceId;
    return new NextResponse(bytes, {
      status: 200,
      headers: {
        "content-type": "application/zip",
        "content-disposition": `attachment; filename="ship-knowledge-${safePathPart(workspaceSlug)}.zip"`,
        "cache-control": "no-store",
      },
    });
  } catch (error) {
    if (error instanceof ApiHttpError) {
      return NextResponse.json(
        { error: error.message, detail: error.detail },
        { status: error.status },
      );
    }
    throw error;
  }
}

function renderArticleMarkdown(
  bucketName: string,
  article: Awaited<ReturnType<typeof listBucketArticles>>[number],
): string {
  const frontmatter = [
    "---",
    `title: ${JSON.stringify(article.title)}`,
    `slug: ${JSON.stringify(article.slug)}`,
    `bucket: ${JSON.stringify(bucketName)}`,
    `version: ${article.version}`,
    `status: ${JSON.stringify(article.status)}`,
    `created_at: ${JSON.stringify(article.created_at)}`,
    `updated_at: ${JSON.stringify(article.updated_at)}`,
    "---",
    "",
  ].join("\n");
  return `${frontmatter}${article.body_md.trim()}\n`;
}

function safePathPart(value: string): string {
  const slug = value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug || "untitled";
}

