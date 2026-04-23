/**
 * POST /api/inbox-routing/update — patch an inbox routing rule.
 *
 * Server-action endpoint for the /settings/inbox-routing editor pane.
 * Accepts the same form shape as /create (minus the immutable
 * `handle` field) plus a `rule_id`, then forwards to
 * `PATCH /v1/workspaces/{ws}/inbox/routing/{rule_id}`.
 *
 * The backend re-runs the full cross-field validator on the
 * post-patch row, so we always send the relevant target_* fields
 * from scratch (and explicitly null the inactive ones) to avoid the
 * row's prior state confusing the validator on a target_type flip.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  updateInboxRoutingRule,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";
import type {
  InboxAssignmentStrategy,
  InboxRoutingTargetType,
} from "@/lib/inbox-types";

const VALID_TARGET_TYPES: InboxRoutingTargetType[] = ["user", "group", "strategy"];
const VALID_STRATEGIES = ["round_robin", "oncall", "first"] as const;
type Strategy = (typeof VALID_STRATEGIES)[number];

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const ruleId = (form.get("rule_id") ?? "").toString();
  const targetTypeRaw = (form.get("target_type") ?? "").toString();
  const targetUserId = (form.get("target_user_id") ?? "").toString().trim();
  const targetGroupId = (form.get("target_group_id") ?? "").toString().trim();
  const targetStrategy = (form.get("target_strategy") ?? "").toString().trim();
  const assignmentRaw = (form.get("assignment_strategy") ?? "").toString().trim();
  const isEnabled = (form.get("is_enabled") ?? "").toString() === "1";

  if (!wsId || !ruleId || !targetTypeRaw) return back(origin, "bad_input", ruleId);
  if (!VALID_TARGET_TYPES.includes(targetTypeRaw as InboxRoutingTargetType)) {
    return back(origin, "validation_failed", ruleId);
  }
  const targetType = targetTypeRaw as InboxRoutingTargetType;

  let body: Parameters<typeof updateInboxRoutingRule>[2];
  if (targetType === "user") {
    if (!targetUserId) return back(origin, "validation_failed", ruleId);
    body = {
      target_type: "user",
      target_user_id: targetUserId,
      target_group_id: null,
      target_strategy: null,
      assignment_strategy: null,
      is_enabled: isEnabled,
    };
  } else if (targetType === "group") {
    if (!targetGroupId) return back(origin, "validation_failed", ruleId);
    let assignment: InboxAssignmentStrategy | null = null;
    if (assignmentRaw) {
      if (!VALID_STRATEGIES.includes(assignmentRaw as Strategy)) {
        return back(origin, "validation_failed", ruleId);
      }
      assignment = assignmentRaw as InboxAssignmentStrategy;
    }
    body = {
      target_type: "group",
      target_user_id: null,
      target_group_id: targetGroupId,
      target_strategy: null,
      assignment_strategy: assignment,
      is_enabled: isEnabled,
    };
  } else {
    if (!targetStrategy) return back(origin, "validation_failed", ruleId);
    if (!VALID_STRATEGIES.includes(targetStrategy as Strategy)) {
      return back(origin, "validation_failed", ruleId);
    }
    body = {
      target_type: "strategy",
      target_user_id: null,
      target_group_id: null,
      target_strategy: targetStrategy,
      assignment_strategy: null,
      is_enabled: isEnabled,
    };
  }

  if (!isApiConfigured()) return back(origin, "api_unavailable", ruleId);

  try {
    await updateInboxRoutingRule(wsId, ruleId, body);
  } catch (err) {
    return mapError(origin, err, ruleId);
  }

  const url = new URL("/settings/inbox-routing", origin);
  url.searchParams.set("rule", ruleId);
  url.searchParams.set("saved", "1");
  return NextResponse.redirect(url, 303);
}

function back(origin: string, code: string, ruleId?: string) {
  const url = new URL("/settings/inbox-routing", origin);
  if (ruleId) url.searchParams.set("rule", ruleId);
  url.searchParams.set("error", code);
  return NextResponse.redirect(url, 303);
}

function mapError(origin: string, err: unknown, ruleId: string) {
  if (err instanceof ApiUnavailableError) return back(origin, "api_unavailable", ruleId);
  if (err instanceof ApiHttpError) {
    if (err.status === 401) {
      return NextResponse.redirect(
        new URL("/login?error=session_expired", origin),
        303,
      );
    }
    if (err.status === 403) return back(origin, "forbidden", ruleId);
    if (err.status === 404) return back(origin, "not_found", ruleId);
    if (err.status === 409) return back(origin, "duplicate", ruleId);
    if (err.status === 422) {
      const detail =
        typeof err.detail === "string" ? err.detail.toLowerCase() : "";
      if (detail.includes("user is not a workspace member"))
        return back(origin, "target_user_not_member", ruleId);
      if (detail.includes("group does not exist"))
        return back(origin, "target_group_not_workspace", ruleId);
      return back(origin, "validation_failed", ruleId);
    }
    return back(origin, `http_${err.status}`, ruleId);
  }
  return back(origin, "unknown", ruleId);
}
