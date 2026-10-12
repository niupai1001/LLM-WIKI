"""Daily macOS LaunchAgent and a locked, observable one-shot collection job.

Calendar triggers use the macOS system timezone, not the child's TZ variable.
The installer refuses a currently different UTC offset. Power-off catch-up is
not guaranteed; a logged-in user's LaunchAgent is not a system daemon.
"""
import argparse
import fcntl
import hashlib
import json
import os
import plistlib
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .common import ROOT

PROJECT = Path(__file__).resolve().parent.parent
BEIJING = ZoneInfo("Asia/Shanghai")


def paths(project=PROJECT, home=None):
    project = Path(project).resolve()
    data = ROOT if ROOT.is_absolute() else project / ROOT
    label = "com.pi-intel-wiki.daily-" + hashlib.sha256(str(project).encode()).hexdigest()[:8]
    return {"project": project, "python": project / ".venv" / "bin" / "python",
            "logs": data / "logs", "state": data / "logs" / "scheduler-status.json",
            "lock": data / "logs" / "scheduler.lock", "label": label,
            "plist": Path(home or Path.home()) / "Library" / "LaunchAgents" / (label + ".plist")}


def next_run(now=None):
    now = (now or datetime.now(BEIJING)).astimezone(BEIJING)
    target = now.replace(hour=8, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target.isoformat(timespec="seconds")


def timezone_matches_beijing(now=None):
    now = now or datetime.now().astimezone()
    return now.utcoffset() == now.astimezone(BEIJING).utcoffset()


def system_timezone():
    resolved = str(Path("/etc/localtime").resolve())
    if "/zoneinfo/" in resolved:
        return resolved.split("/zoneinfo/", 1)[1]
    return str(datetime.now().astimezone().tzinfo)


def build_plist(project=PROJECT, home=None):
    p = paths(project, home)
    return {"Label": p["label"],
            "ProgramArguments": [str(p["python"]), "-m", "station.scheduler", "run"],
            "WorkingDirectory": str(p["project"]),
            "EnvironmentVariables": {"TZ": "Asia/Shanghai", "PYTHONUNBUFFERED": "1",
                                     "STATION_DATA": str(p["logs"].parent)},
            "StartCalendarInterval": {"Hour": 8, "Minute": 0},
            "RunAtLoad": False,
            "StandardOutPath": str(p["logs"] / "launchd.stdout.log"),
            "StandardErrorPath": str(p["logs"] / "launchd.stderr.log")}


def _stamp():
    return datetime.now(BEIJING).isoformat(timespec="seconds")


def _save(path, value):
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _locked(path):
    if not path.exists():
        return False
    with path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(lock, fcntl.LOCK_UN)
        return False


def get_status(project=PROJECT, home=None, include_launchd=False):
    """Small, secret-free API for the web UI; it never starts collection."""
    p = paths(project, home)
    state = {}
    if p["state"].exists():
        try:
            state = json.loads(p["state"].read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {"status": "unknown"}
    running = _locked(p["lock"])
    if isinstance(state, dict) and state.get("status") == "running" and not running:
        state = {**state, "status": "interrupted", "reason": "process-ended-without-completion"}
    timezone_matches = timezone_matches_beijing()
    result = {"installed": p["plist"].is_file(), "enabled": p["plist"].is_file(), "label": p["label"],
              "time": "08:00", "schedule": "每天 08:00", "timezone": "Asia/Shanghai",
              "system_timezone": system_timezone(), "timezone_matches": timezone_matches,
              "system_timezone_matches": timezone_matches,
              "next_run": next_run(), "running": running,
              "last_run": state, "log_path": str(p["logs"] / "scheduler.log"),
              "catch_up_guaranteed": False}
    if include_launchd:
        result["loaded"] = False
        if sys.platform == "darwin":
            try:
                probe = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{p['label']}"],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
                result["loaded"] = probe.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                result["loaded"] = None
    return result


def status(project=PROJECT, home=None, include_launchd=False):
    """UI-friendly alias for get_status."""
    return get_status(project, home, include_launchd)


def run_once(project=PROJECT, home=None, runner=None, auto_draft=None):
    """Run ingest once, optionally prepare. Concurrent calls return without work.

    Output is retained in the project log, while status contains only generic
    results. Feed errors and model warnings also fail the corresponding step,
    because these CLI commands can catch errors and still exit with code zero.
    """
    p = paths(project, home)
    p["logs"].mkdir(parents=True, exist_ok=True)
    runner = runner or subprocess.run
    auto_draft = os.getenv("AUTO_DRAFT", "false").lower() == "true" if auto_draft is None else auto_draft
    with p["lock"].open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "skipped", "reason": "already-running"}
        state = {"status": "running", "phase": "ingest", "started_at": _stamp(), "finished_at": None,
                 "pid": os.getpid(), "steps": []}
        _save(p["state"], state)
        env = dict(os.environ, TZ="Asia/Shanghai", PYTHONUNBUFFERED="1",
                   STATION_DATA=str(p["logs"].parent))
        with (p["logs"] / "scheduler.log").open("a", encoding="utf-8") as log:
            log.write(f"[{state['started_at']}] collection started\n")
            log.flush()
            try:
                for action in ["ingest"] + (["prepare"] if auto_draft else []):
                    state["phase"] = action
                    _save(p["state"], state)
                    # Retain CLI diagnostics separately from the public status object.
                    completed = runner([str(p["python"]), "-m", "station.cli", action],
                                       cwd=str(p["project"]), env=env,
                                       pass_fds=(lock.fileno(),),
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                    output = completed.stdout or ""
                    log.write(output)
                    log.flush()
                    warning_failure = bool(re.search(r"^\[ERROR\]|^\[WARN\] Could not (?:compile|prepare|publish)", output, re.M))
                    failed = completed.returncode != 0 or warning_failure
                    state["steps"].append({"action": action, "exit_code": completed.returncode,
                                           "status": "failed" if failed else "succeeded"})
                    _save(p["state"], state)
                    if failed:
                        state["status"] = "failed"
                        state["reason"] = "command-failed" if completed.returncode else "reported-errors"
                        break
                else:
                    state["status"] = "succeeded"
            except Exception as exc:
                state["status"] = "failed"
                state["reason"] = "execution-error"
                # Do not serialize exception messages: they can contain private URLs.
                state["error_type"] = type(exc).__name__
            finally:
                state["finished_at"] = _stamp()
                state["phase"] = "finished"
                _save(p["state"], state)
                log.write(f"[{state['finished_at']}] collection {state['status']}\n")
                log.flush()
        return state


def install(project=PROJECT, home=None):
    """Explicit installation only; importing this module never changes launchd."""
    if sys.platform != "darwin":
        raise RuntimeError("LaunchAgent installation requires macOS")
    if not timezone_matches_beijing():
        raise RuntimeError("Set the macOS system timezone to Asia/Shanghai before installing the 08:00 calendar trigger")
    p = paths(project, home)
    if not p["python"].is_file():
        raise RuntimeError("Project .venv/bin/python is missing")
    p["logs"].mkdir(parents=True, exist_ok=True)
    p["plist"].parent.mkdir(parents=True, exist_ok=True)
    domain = f"gui/{os.getuid()}"
    service = domain + "/" + p["label"]
    # Replacing an installed task is explicit, reversible, and does not run it.
    subprocess.run(["launchctl", "bootout", service], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with p["plist"].open("wb") as file:
        plistlib.dump(build_plist(project, home), file)
    subprocess.run(["launchctl", "enable", service], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["launchctl", "bootstrap", domain, str(p["plist"])], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return get_status(project, home, include_launchd=True)


def main():
    parser = argparse.ArgumentParser(description="Daily 08:00 Beijing collection LaunchAgent")
    parser.add_argument("action", choices=["install", "status", "run"])
    args = parser.parse_args()
    try:
        if args.action == "install":
            result = install()
        elif args.action == "status":
            result = get_status(include_launchd=True)
        else:
            result = run_once()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result.get("status") == "failed" else 0
    except Exception as exc:
        print(json.dumps({"status": "failed", "reason": "scheduler-error", "error_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
