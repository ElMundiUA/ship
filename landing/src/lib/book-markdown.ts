/**
 * Thin TypeScript re-export of the book-markdown helpers.
 * The actual logic lives in book-markdown.mjs so it can be reused
 * by the static PDF builder without a TS runner.
 */
export {
  transformAdmonitions,
  transformHeadingIds,
  rewriteDocLinks,
  rewriteImages,
  injectBookCharts,
  bookChartInsertions,
  preprocessBookMarkdown,
} from "./book-markdown.mjs";
