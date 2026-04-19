#!/usr/bin/env node
/**
 * Render Apache ECharts infographics as static SVG so the printable PDF
 * can embed them next to the chapters that motivate them.
 *
 * Output: landing/public/diagrams/charts/*.svg
 *
 * Run from repo root:
 *   node landing/scripts/build-book-charts.mjs
 *
 * The script uses ECharts' SSR / renderer="svg" mode so we never need a
 * DOM or a chromium binary at chart-generation time. All charts are
 * intentionally designed in the book's warm cream/ink palette so the PDF
 * pipeline can render them directly without colour inversion.
 *
 * Charts produced (kept boring on purpose — the prose is the show, the
 * chart is the receipt):
 *
 *   - book-morning-metrics.svg   → Ch. 20.A "What to measure in the morning"
 *   - book-elm64-compound.svg    → Ch. 25.A and Ch. 40 (the ELM-64 field note)
 *   - book-bounded-throughput.svg → Ch. 5 "Throughput must be bounded"
 *   - book-role-grid.svg         → Ch. 19 "Why 'always on' is a trap"
 *   - book-cost-envelope.svg     → Ch. 31.A "The price of a bounded loop"
 */
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import * as echarts from "echarts";

const __dirname = dirname(fileURLToPath(import.meta.url));
const landingRoot = join(__dirname, "..");
const repoRoot = join(landingRoot, "..");
const outDir = join(landingRoot, "public", "diagrams", "charts");
mkdirSync(outDir, { recursive: true });

/* ----------------------------- palette ------------------------------- *
 * Mirrors landing/scripts/build-book-pdf.mjs CSS variables so the
 * charts feel like part of the page, not a screenshot from a SaaS
 * dashboard. Update both files together if you redesign the print
 * theme. */
const PAPER = "#fbf8f1";
const PAPER_SOFT = "#f4efe1";
const INK = "#1b1d24";
const INK_SOFT = "#4d525c";
const INK_FAINT = "#7b818d";
const RULE = "#d9d2c3";
const ACCENT = "#a4451b"; // burnt sienna (h2 underline + drop cap)
const ACCENT_SOFT = "#b96b3f";
const ACCENT_PALE = "#e8c9a8";
const QUOTE_BAR = "#c98a4f";
const INK_GREEN = "#3d6b4f"; // for "healthy" / merged signals
const INK_GREEN_SOFT = "#74a087";
const INK_RED = "#9a2f2a"; // for "incident" / rework signals

const SERIF = "Iowan Old Style, Charter, Source Serif Pro, Georgia, Times New Roman, serif";
const SANS = "Inter, Helvetica Neue, Arial, sans-serif";
const MONO = "JetBrains Mono, SF Mono, Menlo, Consolas, monospace";

/** Render one option to disk; throws if echarts fails. */
function render(name, option, width, height) {
  const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width, height });
  /* baseline for every chart: cream paper, no chart-level title (titles
   * live in the printed figcaption so the typography matches the body
   * text). */
  chart.setOption({
    backgroundColor: "transparent",
    textStyle: { fontFamily: SANS, color: INK },
    animation: false,
    ...option,
  });
  const svg = chart.renderToSVGString();
  chart.dispose();
  /* Some downstream PDF renderers refuse SVGs without xmlns; ECharts
   * already adds the right namespaces, but we strip width/height attrs
   * so the <img> CSS can scale freely on the page. */
  const cleaned = svg
    .replace(/\swidth="\d+"/, "")
    .replace(/\sheight="\d+"/, "")
    .replace(/<svg /, `<svg preserveAspectRatio="xMidYMid meet" `);
  const filePath = join(outDir, `${name}.svg`);
  writeFileSync(filePath, cleaned);
  console.log("build-book-charts:", filePath);
}

/* --------------------- Chart 1: morning metrics --------------------- *
 * A small-multiples-style line chart for the five "morning shape"
 * signals named in chapter 20.A. Points are illustrative — the chart
 * is meant to teach the *rhythm* of the panel, not to publish real
 * data. */
{
  const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const pickRate = [26, 24, 27, 25, 18, 8, 6]; // %
  const drift = [0, 1, 0, 2, 5, 1, 0];
  const feedback = [1, 0, 2, 1, 3, 0, 0];
  const flake = [3, 4, 2, 6, 9, 4, 2]; // % rerun
  const cost = [38, 41, 39, 44, 71, 30, 24]; // % of envelope
  render(
    "book-morning-metrics",
    {
      grid: { left: 56, right: 24, top: 36, bottom: 32, containLabel: true },
      legend: {
        top: 0,
        left: 0,
        textStyle: { color: INK_SOFT, fontFamily: SANS, fontSize: 11 },
        itemWidth: 14,
        itemHeight: 8,
        itemGap: 18,
        data: ["Pick rate %", "Artifact drift", "Feedback emitted", "E2E flake %", "Cost % of envelope"],
      },
      xAxis: {
        type: "category",
        data: days,
        axisLine: { lineStyle: { color: RULE } },
        axisTick: { show: false },
        axisLabel: { color: INK_SOFT, fontFamily: SANS },
      },
      yAxis: {
        type: "value",
        splitLine: { lineStyle: { color: RULE, type: "dashed" } },
        axisLine: { show: false },
        axisLabel: { color: INK_FAINT, fontFamily: SANS, formatter: "{value}" },
      },
      series: [
        {
          name: "Pick rate %",
          type: "line",
          smooth: true,
          symbol: "circle",
          symbolSize: 7,
          lineStyle: { color: ACCENT, width: 2.4 },
          itemStyle: { color: ACCENT },
          markArea: {
            silent: true,
            itemStyle: { color: ACCENT_PALE, opacity: 0.28 },
            data: [[{ yAxis: 22 }, { yAxis: 28 }]],
          },
          data: pickRate,
        },
        {
          name: "Artifact drift",
          type: "line",
          smooth: true,
          symbol: "circle",
          symbolSize: 6,
          lineStyle: { color: INK_SOFT, width: 1.6 },
          itemStyle: { color: INK_SOFT },
          data: drift,
        },
        {
          name: "Feedback emitted",
          type: "line",
          smooth: true,
          symbol: "diamond",
          symbolSize: 7,
          lineStyle: { color: INK_GREEN, width: 1.6 },
          itemStyle: { color: INK_GREEN },
          data: feedback,
        },
        {
          name: "E2E flake %",
          type: "line",
          smooth: true,
          symbol: "triangle",
          symbolSize: 7,
          lineStyle: { color: ACCENT_SOFT, width: 1.8, type: "dashed" },
          itemStyle: { color: ACCENT_SOFT },
          data: flake,
        },
        {
          name: "Cost % of envelope",
          type: "line",
          smooth: true,
          symbol: "rect",
          symbolSize: 7,
          lineStyle: { color: INK_RED, width: 1.8 },
          itemStyle: { color: INK_RED },
          markLine: {
            symbol: "none",
            lineStyle: { color: INK_RED, type: "dotted", opacity: 0.55 },
            data: [{ yAxis: 100, label: { show: true, formatter: "envelope ceiling", color: INK_FAINT, fontSize: 10 } }],
          },
          data: cost,
        },
      ],
    },
    760,
    360,
  );
}

/* ---------------- Chart 2: ELM-64 compound-interest ---------------- *
 * A bar-on-timeline showing how a single artifact bug ("match on Slack
 * error English") shipped fifteen patches in one calendar day before
 * anyone escalated to a design fix. The "should-have-escalated" marker
 * sits at commit #3 — that's the chapter's whole point. */
{
  const days = ["Mar 12", "Mar 13", "Mar 14", "Mar 15", "Mar 16", "Mar 17", "Mar 18"];
  const fixes = [1, 2, 0, 1, 15, 4, 0];
  /* the "right answer" line: by commit #3 on Mar 16 the on-call should
   * have flipped from "patch the symptom" to "escalate the artifact". */
  const escalations = [0, 0, 0, 0, 1, 0, 0];
  render(
    "book-elm64-compound",
    {
      grid: { left: 50, right: 24, top: 38, bottom: 32, containLabel: true },
      legend: {
        top: 0,
        left: 0,
        textStyle: { color: INK_SOFT, fontFamily: SANS, fontSize: 11 },
        itemWidth: 14,
        itemHeight: 8,
        itemGap: 18,
      },
      xAxis: {
        type: "category",
        data: days,
        axisLine: { lineStyle: { color: RULE } },
        axisTick: { show: false },
        axisLabel: { color: INK_SOFT, fontFamily: SANS },
      },
      yAxis: {
        type: "value",
        name: "commits / day",
        nameTextStyle: { color: INK_FAINT, fontFamily: SANS, fontSize: 11, padding: [0, 0, 8, 0] },
        splitLine: { lineStyle: { color: RULE, type: "dashed" } },
        axisLine: { show: false },
        axisLabel: { color: INK_FAINT, fontFamily: SANS },
      },
      series: [
        {
          name: "fix(ELM-64) patches",
          type: "bar",
          barWidth: 28,
          itemStyle: { color: ACCENT, borderRadius: [3, 3, 0, 0] },
          label: {
            show: true,
            position: "top",
            color: INK_SOFT,
            fontFamily: SANS,
            fontSize: 11,
            formatter: ({ value }) => (value > 0 ? value : ""),
          },
          data: fixes,
          markPoint: {
            symbol: "pin",
            symbolSize: 56,
            label: {
              color: PAPER,
              fontFamily: SANS,
              fontSize: 10,
              fontWeight: 700,
              formatter: "15× same\nsubject line",
            },
            itemStyle: { color: INK_RED },
            data: [{ xAxis: "Mar 16", yAxis: 15 }],
          },
        },
        {
          name: "Escalation that did not happen",
          type: "scatter",
          symbol: "diamond",
          symbolSize: 18,
          itemStyle: { color: INK_GREEN, borderColor: PAPER, borderWidth: 2 },
          data: [["Mar 16", 3]],
          tooltip: { show: false },
          markLine: {
            symbol: ["none", "arrow"],
            symbolSize: 8,
            lineStyle: { color: INK_GREEN, type: "dashed", width: 1.4 },
            label: {
              show: true,
              color: INK_GREEN,
              fontFamily: SANS,
              fontSize: 10,
              formatter: "by patch #3 → file feedback, escalate the artifact",
              position: "middle",
            },
            data: [
              [
                { name: "should-have-escalated", coord: ["Mar 16", 3] },
                { coord: ["Mar 16", 14] },
              ],
            ],
          },
        },
      ],
    },
    760,
    320,
  );
}

/* ----------------- Chart 3: bounded throughput ----------------- *
 * Two area curves: as you load WIP into the system, "merged
 * outcomes" rise then plateau, while "rework + duplicate PRs"
 * accelerates. The intersection is the chapter's argument. */
{
  const wip = Array.from({ length: 11 }, (_, i) => i); // 0..10 concurrent items
  const merged = wip.map((w) => Math.round(Math.min(8, w * 1.6 - 0.05 * w * w) * 10) / 10);
  const rework = wip.map((w) => Math.round((0.05 * w * w * 1.2) * 10) / 10);
  render(
    "book-bounded-throughput",
    {
      grid: { left: 56, right: 24, top: 38, bottom: 36, containLabel: true },
      legend: {
        top: 0,
        left: 0,
        textStyle: { color: INK_SOFT, fontFamily: SANS, fontSize: 11 },
        itemWidth: 14,
        itemHeight: 8,
        itemGap: 18,
      },
      xAxis: {
        type: "category",
        data: wip.map(String),
        name: "concurrent items in flight",
        nameLocation: "middle",
        nameGap: 28,
        nameTextStyle: { color: INK_FAINT, fontFamily: SANS, fontSize: 11 },
        axisLine: { lineStyle: { color: RULE } },
        axisTick: { show: false },
        axisLabel: { color: INK_SOFT, fontFamily: SANS },
      },
      yAxis: {
        type: "value",
        splitLine: { lineStyle: { color: RULE, type: "dashed" } },
        axisLine: { show: false },
        axisLabel: { color: INK_FAINT, fontFamily: SANS, formatter: "{value}" },
      },
      series: [
        {
          name: "Merged outcomes (per day)",
          type: "line",
          smooth: true,
          areaStyle: { color: INK_GREEN_SOFT, opacity: 0.35 },
          lineStyle: { color: INK_GREEN, width: 2.2 },
          itemStyle: { color: INK_GREEN },
          symbol: "none",
          data: merged,
        },
        {
          name: "Rework + duplicate PRs",
          type: "line",
          smooth: true,
          areaStyle: { color: ACCENT_PALE, opacity: 0.5 },
          lineStyle: { color: ACCENT, width: 2.2 },
          itemStyle: { color: ACCENT },
          symbol: "none",
          data: rework,
          markArea: {
            silent: true,
            itemStyle: { color: PAPER_SOFT, opacity: 0.6, borderColor: QUOTE_BAR, borderWidth: 0 },
            label: {
              color: INK_SOFT,
              fontFamily: SANS,
              fontSize: 11,
              fontWeight: 600,
              position: "insideTop",
              formatter: "bounded zone — what we ship",
            },
            data: [[{ xAxis: "0" }, { xAxis: "5" }]],
          },
          markLine: {
            symbol: "none",
            lineStyle: { color: QUOTE_BAR, type: "dashed", width: 1.4 },
            label: {
              show: true,
              color: ACCENT,
              fontFamily: SANS,
              fontSize: 11,
              fontWeight: 600,
              formatter: "throughput knee",
              position: "end",
            },
            data: [{ xAxis: "5" }],
          },
        },
      ],
    },
    760,
    340,
  );
}

/* ----------------- Chart 4: role grid (heatmap) ---------------- *
 * Visualises chapter 19's argument: assign at most one delivery role
 * to each UTC slot. The "firehose" alternative would smear every
 * role across every minute and produce duplicate PRs. */
{
  const hours = Array.from({ length: 12 }, (_, i) => `${String(i * 2).padStart(2, "0")}:00`);
  const roles = ["Intake", "Clarify", "Analyse", "Develop"];
  /* value semantics: 0 = idle, 1..4 = which role owns this 2h slot.
   * Each row gets exactly one "1" per slot — the grid never lets two
   * roles fire in the same window. */
  const data = [];
  for (let h = 0; h < hours.length; h += 1) {
    const owner = h % 4; // round-robin every 2 hours
    for (let r = 0; r < roles.length; r += 1) {
      data.push([h, r, r === owner ? r + 1 : 0]);
    }
  }
  render(
    "book-role-grid",
    {
      grid: { left: 80, right: 24, top: 36, bottom: 56, containLabel: false },
      tooltip: { show: false },
      visualMap: {
        show: false,
        type: "piecewise",
        pieces: [
          { value: 0, color: PAPER_SOFT },
          { value: 1, color: ACCENT_PALE },
          { value: 2, color: QUOTE_BAR },
          { value: 3, color: ACCENT_SOFT },
          { value: 4, color: ACCENT },
        ],
      },
      xAxis: {
        type: "category",
        data: hours,
        name: "UTC hour",
        nameLocation: "middle",
        nameGap: 32,
        nameTextStyle: { color: INK_FAINT, fontFamily: SANS, fontSize: 11 },
        splitArea: { show: false },
        axisLine: { lineStyle: { color: RULE } },
        axisTick: { show: false },
        axisLabel: { color: INK_SOFT, fontFamily: SANS, fontSize: 10 },
      },
      yAxis: {
        type: "category",
        data: roles,
        splitArea: { show: false },
        axisLine: { lineStyle: { color: RULE } },
        axisTick: { show: false },
        axisLabel: { color: INK, fontFamily: SANS, fontSize: 11, fontWeight: 600 },
      },
      series: [
        {
          type: "heatmap",
          data,
          itemStyle: { borderColor: PAPER, borderWidth: 2 },
          label: {
            show: true,
            color: PAPER,
            fontFamily: SANS,
            fontSize: 10,
            fontWeight: 700,
            formatter: ({ value }) => (value[2] === 0 ? "" : "● run"),
          },
        },
      ],
    },
    760,
    260,
  );
}

/* ----------------- Chart 5: cost envelope ---------------- *
 * Bars = real agent runs per day, dashed line = the bounded envelope.
 * The point is to make "we are sized for N runs" auditable as a
 * picture, not a paragraph. */
{
  const days = ["W1·Mon", "W1·Tue", "W1·Wed", "W1·Thu", "W1·Fri", "W2·Mon", "W2·Tue", "W2·Wed", "W2·Thu", "W2·Fri"];
  const runs = [40, 44, 38, 47, 49, 42, 46, 39, 51, 78];
  const envelope = days.map(() => 60);
  render(
    "book-cost-envelope",
    {
      grid: { left: 56, right: 24, top: 36, bottom: 36, containLabel: true },
      legend: {
        top: 0,
        left: 0,
        textStyle: { color: INK_SOFT, fontFamily: SANS, fontSize: 11 },
        itemWidth: 14,
        itemHeight: 8,
        itemGap: 22,
        padding: [0, 0, 0, 0],
      },
      xAxis: {
        type: "category",
        data: days,
        axisLine: { lineStyle: { color: RULE } },
        axisTick: { show: false },
        axisLabel: { color: INK_SOFT, fontFamily: SANS, fontSize: 10 },
      },
      yAxis: {
        type: "value",
        name: "agent runs / day",
        nameTextStyle: { color: INK_FAINT, fontFamily: SANS, fontSize: 11 },
        splitLine: { lineStyle: { color: RULE, type: "dashed" } },
        axisLine: { show: false },
        axisLabel: { color: INK_FAINT, fontFamily: SANS },
      },
      series: [
        {
          name: "Agent runs",
          type: "bar",
          barWidth: 26,
          itemStyle: {
            color: ({ data, dataIndex }) => (data > envelope[dataIndex] ? INK_RED : ACCENT_SOFT),
            borderRadius: [3, 3, 0, 0],
          },
          label: {
            show: true,
            position: "top",
            color: INK_SOFT,
            fontFamily: SANS,
            fontSize: 10,
            formatter: ({ value }) => value,
          },
          data: runs,
        },
        {
          name: "Bounded envelope (~60/day)",
          type: "line",
          symbol: "none",
          smooth: false,
          step: "middle",
          lineStyle: { color: ACCENT, type: "dashed", width: 1.6 },
          data: envelope,
        },
      ],
    },
    760,
    320,
  );
}

console.log("build-book-charts: done.");
