"""Phase 2 of the FSM event-driven rearchitecture — kill the crons.

Pin that the 15-min ``fsm_self_heal_scan`` and hourly ``self_heal``
crons are GONE from the scheduler. The Linear-label freeze from
Phase 1 (PR #341) makes the periodic backstop redundant: a ticket
that gets ``outcome=blocked`` carries the freeze label, and the
picker drops it on every subsequent dispatch. Re-firing every 15 min
just lit the picker up to do the same refusal over and over.

Daily-digest + weekly-audit ticks keep firing — they're operator-
facing rollups, not dispatch crons. The lock ids (1015/1016) stay
reserved in :class:`CronLockId` so old ``cron_leases`` rows don't
collide with future numbers.
"""

from __future__ import annotations

from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[3]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_fsm_self_heal_scan_registration_is_gone() -> None:
    src = _read(
        _REPO_ROOT / "apps" / "backend" / "app" / "services" / "cron_jobs.py"
    )
    # No registration anywhere in cron_jobs
    assert "fsm_self_heal_scan" not in src or src.count("fsm_self_heal_scan") == 0, (
        "Re-registering fsm_self_heal_scan resurrects the 15-min "
        "backstop the Phase 1 label freeze made redundant. The audit "
        "noise + wasted runner-minutes return with it. If the freeze "
        "isn't sufficient for some new failure mode, file an inbox "
        "letter from the failure path instead."
    )
    # The tick coroutine is also gone
    assert "_fsm_self_heal_scan_tick" not in src


def test_hourly_self_heal_workspace_bundle_is_gone() -> None:
    src = _read(
        _REPO_ROOT / "apps" / "backend" / "app" / "services" / "daily_scheduler.py"
    )
    assert "_self_heal_tick" not in src, (
        "Hourly self-heal bundle is the biggest cron-clock source by "
        "event volume — one fanout dispatch per workspace per hour, "
        "regardless of whether anything had changed. Reinstating it "
        "needs an explicit reason; today's design is event-driven."
    )
    # Daily + weekly digests must still register — they're operator
    # rollups, not dispatch crons.
    assert "_daily_digest_tick" in src
    assert "_weekly_audit_tick" in src


def test_scan_eligible_tickets_export_is_gone() -> None:
    from backend.app.services import fsm_self_heal

    assert not hasattr(fsm_self_heal, "scan_eligible_tickets")
    assert not hasattr(fsm_self_heal, "_scan_one_workspace")
    # Runner-fail detectors went with it — the label freeze covers
    # the same "ticket is wedged" symptom one cycle earlier.
    for name in (
        "_looks_like_runner_fail_loop",
        "_file_runner_fail_blocker",
        "_looks_like_workspace_runner_fail",
        "_file_workspace_runner_fail_blocker",
        "SCAN_STAGES",
        "STALE_DISPATCH_WINDOW",
        "RUNNER_FAIL_THRESHOLD",
        "RUNNER_FAIL_WINDOW",
        "WORKSPACE_RUNNER_FAIL_ROLLUP_THRESHOLD",
    ):
        assert not hasattr(fsm_self_heal, name), (
            f"{name} should be deleted. The Phase 1 label freeze + "
            "Linear webhook-driven cascade replace the entire "
            "scan/runner-fail detector pipeline."
        )


def test_auto_reprovision_on_startup_kept() -> None:
    # Single surviving function in fsm_self_heal — runs from FastAPI
    # lifespan to keep workspaces' label_id_by_stage in sync when
    # SHIP_FSM_STAGES grows in code. Independent of the cron-driven
    # scan; pin so a future cleanup pass doesn't sweep it out.
    from backend.app.services.fsm_self_heal import auto_reprovision_on_startup

    assert callable(auto_reprovision_on_startup)


def test_cron_lock_ids_for_deleted_crons_remain_reserved() -> None:
    # WORKSPACE_SELF_HEAL=1015 + FSM_SCAN_BACKSTOP=1016 are deleted
    # from the enum but the *numbers* should stay reserved so an
    # accidental reuse can't collide with stale ``cron_leases`` rows
    # from before Phase 2. Pin via the tombstone comment in cron.py
    # so a renumbering PR has to explicitly explain itself.
    src = _read(
        _REPO_ROOT / "apps" / "backend" / "app" / "services" / "cron.py"
    )
    # The enum should NOT re-define them as live members
    assert "WORKSPACE_SELF_HEAL = 1015" not in src
    assert "FSM_SCAN_BACKSTOP = 1016" not in src
    # …but the tombstone-comment should keep the numbers visible
    assert "1015" in src and "1016" in src, (
        "Reserved-but-deleted lock ids must stay called out in the "
        "source so a future enum addition doesn't accidentally reuse "
        "them and collide with stale cron_leases rows."
    )
