/**
 * POST /api/inbox-routing/create — create an inbox routing rule.
 *
 * Server-action endpoint for the /settings/inbox-routing page. Parses
 * the form payload (which differs by `target_type`), validates the
 * XOR shape on the client side too so we can surface a friendly error
 * before the network round-trip, and forwards to
 * `POST /v1/workspaces/{ws}/inbox/routing`.
 *
 * Mirrors the convention used by /api/inbox-groups/create: bounce
 * back to the page with a query-string error code on failure so the
 * server component can render a human-readable banner.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  createInboxRoutingRule,
  isApiConfigured,
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
  const handle = (form.get("handle") ?? "").toString().trim().toLowerCase();
  const targetTypeRaw = (form.get("target_type") ?? "").toString();
  const targetUserId = (form.get("target_user_id") ?? "").toString().trim();
  const targetGroupId = (form.get("target_group_id") ?? "").toString().trim();
  const targetStrategy = (form.get("target_strategy") ?? "").toString().trim();
  const assignmentRaw = (form.get("assignment_strategy") ?? "").toString().trim();
  const isEnabled = (form.get("is_enabled") ?? "").toString() === "1";

  if (!wsId || !handle || !targetTypeRaw) return back(origin, "bad_input");
  if (!/^[a-z][a-z0-9_]*$/.test(handle)) return back(origin, "validation_failed");
  if (!VALID_TARGET_TYPES.includes(targetTypeRaw as InboxRoutingTargetType)) {
    return back(origin, "validation_failed");
  }
  const targetType = targetTypeRaw as InboxRoutingTargetType;

  // Mirror backend cross-field invariants client-side so we never POST
  // a payload the API will 422 on.
  let body: Parameters<typeof createInboxRoutingRule>[1];
  if (targetType === "user") {
    if (!targetUserId) return back(origin, "validation_failed");
    body = {
      handle,
      target_type: "user",
      target_user_id: targetUserId,
      is_enabled: isEnabled,
    };
  } else if (targetType === "group") {
    if (!targetGroupId) return back(origin, "validation_failed");
    let assignment: InboxAssignmentStrategy | null = null;
    if (assignmentRaw) {
      if (!VALID_STRATEGIES.includes(assignmentRaw as Strategy)) {
        return back(origin, "validation_failed");
      }
      assignment = assignmentRaw as InboxAssignmentStrategy;
    }
    body = {
      handle,
      target_type: "group",
      target_group_id: targetGroupId,
      assignment_strategy: assignment,
      is_enabled: isEnabled,
    };
  } else {
    if (!targetStrategy) return back(origin, "validation_failed");
    if (!VALID_STRATEGIES.includes(targetStrategy as Strategy)) {
      return back(origin, "validation_failed");
    }
    body = {
      handle,
      target_type: "strategy",
      target_strategy: targetStrategy,
      is_enabled: isEnabled,
    };
  }

  if (!isApiConfigured()) return back(origin, "api_unavailable");

  try {
    await createInboxRoutingRule(wsId, body);
  } catch (err) {
    return mapError(origin, err);
  }

  const url = new URL("/settings/inbox-routing", origin);
  url.searchParams.set("created", handle);
  return NextResponse.redirect(url, 303);
}

function back(origin: string, code: string) {
  const url = new URL("/settings/inbox-routing", origin);
  url.searchParams.set("error", code);
  return NextResponse.redirect(url, 303);
}

function mapError(origin: string, err: unknown) {
  if (err instanceof ApiUnavailableError) return back(origin, "api_unavailable");
  if (err instanceof ApiHttpError) {
    if (err.status === 401) {
      return NextResponse.redirect(
        new URL("/login?error=session_expired", origin),
        303,
      );
    }
    if (err.status === 403) return back(origin, "forbidden");
    if (err.status === 404) return back(origin, "not_found");
    if (err.status === 409) return back(origin, "duplicate");
    if (err.status === 422) {
      const detail =
        typeof err.detail === "string" ? err.detail.toLowerCase() : "";
      if (detail.includes("user is not a workspace member"))
        return back(origin, "target_user_not_member");
      if (detail.includes("group does not exist"))
        return back(origin, "target_group_not_workspace");
      return back(origin, "validation_failed");
    }
    return back(origin, `http_${err.status}`);
  }
  return back(origin, "unknown");
}
