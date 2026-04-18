import { loadArtifactCatalog, loadArtifactBody, type ArtifactEntry } from "@/lib/artifacts-fs";

export type CollectionGroup = "product" | "starter";

export interface CollectionEntry extends ArtifactEntry {
  group: CollectionGroup | string;
}

export interface CollectionsManifest {
  version: number;
  description: string;
  collections: CollectionEntry[];
}

export function loadCollectionsManifest(): CollectionsManifest {
  const cat = loadArtifactCatalog("collections");
  return {
    version: cat.version,
    description: cat.description,
    collections: cat.entries as CollectionEntry[],
  };
}

export function loadCollectionMarkdown(relPath: string): string {
  return loadArtifactBody(relPath);
}

export function getCollectionById(id: string): CollectionEntry | undefined {
  return loadCollectionsManifest().collections.find((c) => c.id === id);
}
