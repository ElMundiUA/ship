import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  type ApiLaneTriggerIn,
  isApiConfigured,
  proposeRepoConfig,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";
import { getSessionToken } from "@/lib/api/session";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const workspaceId = getFormValue(form, "workspaceId");
  const repoId = getFormValue(form, "repoId");
  const stateId = getFormValue(form, "stateId");

  if (!workspaceId || !repoId || !stateId) {
    return back(origin, repoId, stateId, "bad_request");
  }
  if (!isApiConfigured()) {
    return back(origin, repoId, stateId, "api_unavailable");
  }

  try {
    const process = parseObject(getFormValue(form, "processJson"));
    const lanes = normalizeLanesForProposal(
      parseObject(getFormValue(form, "lanesJson")),
    );
    updateState(process, stateId, {
      name: getFormValue(form, "stateName"),
      specialistName: getFormValue(form, "specialistName"),
      instructions: getFormValue(form, "instructions"),
      triggerType: getFormValue(form, "triggerType"),
      triggerDetail: getFormValue(form, "triggerDetail"),
      exitCondition: getFormValue(form, "exitCondition"),
      blockCondition: getFormValue(form, "blockCondition"),
    });

    const token = (await getSessionToken()) ?? undefined;
    const result = await proposeRepoConfig(
      workspaceId,
      repoId,
      {
        lanes,
        process,
        base_sha: getFormValue(form, "baseSha") || null,
        change_summary: `Update process state ${stateId}`,
      },
      token,
    );

    return NextResponse.redirect(result.pr_url, 303);
  } catch (err) {
    if (err instanceof ApiUnavailableError) {
      return back(origin, repoId, stateId, "api_unavailable");
    }
    if (err instanceof ApiHttpError) {
      if (err.status === 401) {
        return NextResponse.redirect(
          new URL("/login?error=session_expired", origin),
          303,
        );
      }
      return back(origin, repoId, stateId, `http_${err.status}`);
    }
    return back(origin, repoId, stateId, "unknown");
  }
}

function back(origin: string, repoId: string, stateId: string, reason: string) {
  const url = new URL("/process", origin);
  if (repoId) url.searchParams.set("repo", repoId);
  if (stateId) url.searchParams.set("state", stateId);
  url.searchParams.set("reason", reason);
  return NextResponse.redirect(url, 303);
}

function getFormValue(form: FormData, key: string): string {
  return (form.get(key) ?? "").toString();
}

function parseObject(raw: string): Record<string, unknown> {
  if (!raw) return {};
  const parsed = JSON.parse(raw) as unknown;
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
  return parsed as Record<string, unknown>;
}

function updateState(
  process: Record<string, unknown>,
  stateId: string,
  patch: {
    name: string;
    specialistName: string;
    instructions: string;
    triggerType: string;
    triggerDetail: string;
    exitCondition: string;
    blockCondition: string;
  },
) {
  const states = Array.isArray(process.states) ? process.states : [];
  for (const item of states) {
    if (!item || typeof item !== "object" || Array.isArray(item)) continue;
    const state = item as Record<string, unknown>;
    if (state.id !== stateId) continue;

    state.name = patch.name;
    state.instructions = patch.instructions;
    state.specialist = {
      ...(isRecord(state.specialist) ? state.specialist : {}),
      name: patch.specialistName,
    };
    state.triggers = [triggerFromForm(patch.triggerType, patch.triggerDetail)];
    state.exit_conditions = [{ expression: patch.exitCondition }];
    state.block_conditions = [{ expression: patch.blockCondition }];
    return;
  }
}

function triggerFromForm(type: string, detail: string) {
  if (type === "schedule") return { type, interval: detail || null, event: null };
  if (type === "event") return { type, interval: null, event: detail || null };
  return { type: "manual", interval: null, event: null };
}

function normalizeLanesForProposal(
  rawLanes: Record<string, unknown>,
): Record<string, ApiLaneTriggerIn> {
  const out: Record<string, ApiLaneTriggerIn> = {};
  for (const [laneId, raw] of Object.entries(rawLanes)) {
    if (!isRecord(raw)) continue;
    const lane: ApiLaneTriggerIn = {};
    const kind = stringValue(raw.kind);
    const schedule = stringValue(raw.schedule) ?? stringValue(raw.cron);
    const event = stringValue(raw.event) ?? stringValue(raw.on);
    const once = stringValue(raw.once);
    if (schedule && (!kind || kind === "schedule")) lane.schedule = schedule;
    else if (event && (!kind || kind === "event")) lane.event = event;
    else if (once && (!kind || kind === "once")) lane.once = once;
    else continue;

    const patterns = Array.isArray(raw.patterns)
      ? raw.patterns.filter((value): value is string => typeof value === "string")
      : null;
    const pattern = stringValue(raw.pattern);
    if (patterns?.length) lane.patterns = patterns;
    else if (pattern) lane.pattern = pattern;

    const fanout = stringValue(raw.fanout);
    if (fanout) lane.fanout = fanout;
    const idempotencyKey =
      stringValue(raw.idempotency_key) ??
      (isRecord(raw.idempotency) ? stringValue(raw.idempotency.key) : null);
    if (idempotencyKey) lane.idempotency_key = idempotencyKey;
    out[laneId] = lane;
  }
  return out;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}
