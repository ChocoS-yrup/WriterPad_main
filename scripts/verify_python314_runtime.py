"""Report which interpreter a stage-F run actually used, and what it installed.

The stage-F risk is not that 3.14 fails loudly. It is that a run quietly falls
back to the system 3.11 and reports a pass that proves nothing. `py -3.14` runs
the system interpreter, not .venv314, so every stage-F command has to name its
interpreter and every result has to carry the interpreter that produced it.

Run it with the interpreter under test:

    .venv314\\Scripts\\python.exe scripts/verify_python314_runtime.py
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone


def runtime_report():
    return {
        "version": sys.version.split()[0],
        "version_full": sys.version,
        "executable": sys.executable,
        "prefix": sys.prefix,
        "base_prefix": sys.base_prefix,
        "in_venv": sys.prefix != sys.base_prefix,
        "stdlib_unicodedata": __import__("unicodedata").unidata_version,
    }


def installed_packages():
    result = subprocess.run(
        [sys.executable, "-m", "pip", "list", "--format=freeze"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    packages = {}
    for line in result.stdout.splitlines():
        if "==" in line:
            name, _, version = line.partition("==")
            packages[name.strip().lower()] = version.strip()
    return packages


def pip_check():
    result = subprocess.run(
        [sys.executable, "-m", "pip", "check"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return {"returncode": result.returncode, "output": result.stdout.strip()}


def storage_name_vectors(root):
    sys.path.insert(0, root)
    from sync_contract import (  # noqa: E402
        SyncContractError,
        normalize_storage_name_v2,
    )

    path = os.path.join(
        root, "sync-contract", "conformance_vectors", "storage-name-v2.json"
    )
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)

    passed = 0
    failures = []
    for vector in payload["vectors"]:
        try:
            result = normalize_storage_name_v2(vector["input"])
            matched = (
                bool(vector["valid"])
                and result.normalized == vector.get("normalized")
                and result.utf8_hex == vector.get("utf8_hex")
            )
        except SyncContractError as error:
            matched = not vector["valid"] and vector.get("error_code", error.code) == error.code
        if matched:
            passed += 1
        else:
            failures.append(vector["vector_id"])
    return {
        "total": len(payload["vectors"]),
        "passed": passed,
        "failures": failures,
        "contract_version": payload.get("contract_version"),
    }


def golden_manifest_replay(root, manifest_path):
    """Recompute every stage-B record here and compare against what 3.11 wrote."""
    if not os.path.exists(manifest_path):
        return {"skipped": "manifest not present"}
    sys.path.insert(0, root)
    from sync_contract import (  # noqa: E402
        SyncContractError,
        normalize_storage_name_v2,
    )

    records = identical = differing = 0
    first_difference = None
    with open(manifest_path, encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            records += 1
            try:
                result = normalize_storage_name_v2(record["name"])
                actual = (result.normalized, result.utf8_hex, None)
            except SyncContractError as error:
                actual = (None, None, error.code)
            expected = (
                record["v2_normalized"],
                record["v2_key_hex"],
                record["v2_reject_code"],
            )
            if actual == expected:
                identical += 1
            else:
                differing += 1
                if first_difference is None:
                    first_difference = {"record_id": record["record_id"]}
    return {
        "records": records,
        "identical": identical,
        "differing": differing,
        "first_difference": first_difference,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=os.getcwd())
    parser.add_argument(
        "--manifest",
        default=os.path.join(
            "_evidence", "python-314-v1-golden-20260920", "manifest.jsonl"
        ),
    )
    parser.add_argument("--out")
    parser.add_argument("--require-python", help="fail unless this exact Python version ran")
    parser.add_argument("--compare-freeze", help="a pip freeze from the other interpreter")
    arguments = parser.parse_args()

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime": runtime_report(),
        "pip_check": pip_check(),
        "packages": installed_packages(),
        "storage_name_v2_vectors": storage_name_vectors(arguments.root),
        "golden_manifest_replay": golden_manifest_replay(
            arguments.root, arguments.manifest
        ),
    }

    if arguments.compare_freeze and os.path.exists(arguments.compare_freeze):
        other = {}
        with open(arguments.compare_freeze, encoding="utf-8") as handle:
            for line in handle:
                if "==" in line:
                    name, _, version = line.partition("==")
                    other[name.strip().lower()] = version.strip()
        mine = report["packages"]
        shared = sorted(set(mine) & set(other))
        report["package_comparison"] = {
            "only_here": sorted(set(mine) - set(other)),
            "only_there": sorted(set(other) - set(mine)),
            "version_differs": {
                name: {"here": mine[name], "there": other[name]}
                for name in shared
                if mine[name] != other[name]
            },
        }

    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if arguments.out:
        os.makedirs(os.path.dirname(os.path.abspath(arguments.out)), exist_ok=True)
        with open(arguments.out, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text + "\n")
    print(text)
    failures = []
    if arguments.require_python and report["runtime"]["version"] != arguments.require_python:
        failures.append("unexpected Python version")
    if report["pip_check"]["returncode"]:
        failures.append("pip check failed")
    if report["storage_name_v2_vectors"]["failures"]:
        failures.append("storage-name-v2 vector mismatch")
    if report["golden_manifest_replay"].get("differing", 0):
        failures.append("golden manifest mismatch")
    if failures:
        raise SystemExit("; ".join(failures))


if __name__ == "__main__":
    main()
