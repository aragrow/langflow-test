#!/usr/bin/env python3
"""
Sync custom component source from disk into Langflow flows.

Langflow embeds a snapshot of every custom component's source code into
the flow at the moment the node is dragged onto the canvas. That snapshot
lives in Langflow's SQLite database (and in any exported flow JSON files).
The runtime executes the snapshot, NOT the .py file on disk — so editing
the .py file alone has no effect on saved flows.

This script walks every flow (in the Langflow DB and in any *.json flow
files in the project root) and replaces each custom-component node's
embedded `template.code.value` with the current disk contents of the
matching .py file under artifact/components/.

Usage:
    python scripts/sync_components.py           # sync DB + all flow JSONs
    python scripts/sync_components.py --check   # report drift without writing
    python scripts/sync_components.py --db-only # only update the Langflow DB
    python scripts/sync_components.py --files-only  # only update .json files

The script is idempotent — re-running after everything is in sync is a
no-op. It's safe to run while Langflow is stopped. If Langflow is running
while the script updates its DB, the changes take effect on next flow
open (Langflow caches flows in memory).
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from hashlib import sha1
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
COMPONENTS_DIR = PROJECT_ROOT / "artifact" / "components"

# The Langflow DB location depends on how Langflow was installed. This
# project puts it inside the venv site-packages (unusual but that's where
# it is). Adjust here if you move it.
LANGFLOW_DB = (
    PROJECT_ROOT
    / ".venv"
    / "lib"
    / "python3.12"
    / "site-packages"
    / "langflow"
    / "langflow.db"
)

# Glob for exported flow JSON files in the project root.
FLOW_JSON_GLOB = "mgr4smb*.json"

DISPLAY_NAME_RE = re.compile(r'display_name\s*=\s*[\'"]([^\'"]+)[\'"]')


def _load_disk_components() -> dict[str, tuple[Path, str]]:
    """Return { display_name_lower: (source_file, source_text) } for every
    custom component .py file under artifact/components/."""
    result: dict[str, tuple[Path, str]] = {}
    if not COMPONENTS_DIR.exists():
        return result

    for py in COMPONENTS_DIR.rglob("*.py"):
        if py.name.startswith("__"):
            continue
        text = py.read_text(encoding="utf-8")
        match = DISPLAY_NAME_RE.search(text)
        if not match:
            continue
        display_name = match.group(1).strip().lower()
        if display_name in result:
            existing = result[display_name][0]
            print(
                f"warning: duplicate display_name {display_name!r} — "
                f"{existing} and {py}",
                file=sys.stderr,
            )
            continue
        result[display_name] = (py, text)
    return result


def _patch_flow_data(
    flow_data: dict,
    components: dict[str, tuple[Path, str]],
) -> list[tuple[str, str, int, int]]:
    """Mutate `flow_data` in place. Return a list of (node_id, display_name,
    old_len, new_len) for every node whose embedded code changed."""
    patched: list[tuple[str, str, int, int]] = []
    for n in flow_data.get("nodes", []):
        nd = n.get("data", {}).get("node", {}) or {}
        dn_raw = nd.get("display_name") or ""
        dn = dn_raw.strip().lower()
        if dn not in components:
            continue
        tmpl = nd.get("template", {}) or {}
        code_field = tmpl.get("code")
        if not isinstance(code_field, dict):
            continue
        old = code_field.get("value", "") or ""
        new = components[dn][1]
        if sha1(old.encode()).hexdigest() == sha1(new.encode()).hexdigest():
            continue
        code_field["value"] = new
        patched.append((n.get("id", "?"), dn_raw, len(old), len(new)))
    return patched


def sync_db(components: dict[str, tuple[Path, str]], check_only: bool) -> int:
    if not LANGFLOW_DB.exists():
        print(f"[db] skipped — {LANGFLOW_DB} not found")
        return 0
    con = sqlite3.connect(LANGFLOW_DB)
    try:
        cur = con.cursor()
        cur.execute("SELECT id, name, data FROM flow")
        rows = cur.fetchall()
        total_changes = 0
        for flow_id, name, data_raw in rows:
            try:
                flow_data = json.loads(data_raw)
            except Exception:
                continue
            # Langflow stores {"nodes": [...], "edges": [...]} directly (no
            # outer "data" wrapper in the DB column).
            patched = _patch_flow_data(flow_data, components)
            if patched:
                print(f"[db] {name!r} ({flow_id}):")
                for nid, dn, old_len, new_len in patched:
                    print(f"     {nid}  {dn}: {old_len} -> {new_len} chars")
                total_changes += len(patched)
                if not check_only:
                    cur.execute(
                        "UPDATE flow SET data = ? WHERE id = ?",
                        (json.dumps(flow_data), flow_id),
                    )
        if not check_only:
            con.commit()
        return total_changes
    finally:
        con.close()


def sync_files(
    components: dict[str, tuple[Path, str]],
    check_only: bool,
) -> int:
    total_changes = 0
    for flow_file in sorted(PROJECT_ROOT.glob(FLOW_JSON_GLOB)):
        try:
            flow = json.loads(flow_file.read_text())
        except Exception as exc:
            print(f"[file] {flow_file.name}: skipped ({exc})")
            continue
        # Exported flow JSONs wrap nodes/edges under `data`.
        container = flow.get("data") if isinstance(flow.get("data"), dict) else flow
        patched = _patch_flow_data(container, components)
        if patched:
            print(f"[file] {flow_file.name}:")
            for nid, dn, old_len, new_len in patched:
                print(f"       {nid}  {dn}: {old_len} -> {new_len} chars")
            total_changes += len(patched)
            if not check_only:
                flow_file.write_text(json.dumps(flow, indent=2))
    return total_changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report drift without modifying anything. Exits 1 if drift exists.",
    )
    parser.add_argument("--db-only", action="store_true", help="Only sync the Langflow DB.")
    parser.add_argument("--files-only", action="store_true", help="Only sync *.json files.")
    args = parser.parse_args()

    components = _load_disk_components()
    if not components:
        print(f"no components found under {COMPONENTS_DIR}", file=sys.stderr)
        return 1

    print(f"found {len(components)} component(s) on disk:")
    for dn, (path, _) in sorted(components.items()):
        print(f"  {dn} -> {path.relative_to(PROJECT_ROOT)}")
    print()

    total = 0
    if not args.files_only:
        total += sync_db(components, check_only=args.check)
    if not args.db_only:
        total += sync_files(components, check_only=args.check)

    if total == 0:
        print("all flows already in sync with disk ✓")
        return 0

    action = "drift found" if args.check else "synced"
    print(f"\n{action}: {total} node(s)")
    return 1 if args.check else 0


if __name__ == "__main__":
    sys.exit(main())
