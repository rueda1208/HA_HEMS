#!/usr/bin/env python3
"""Fetch, compare, and safely experiment with HEMS device configuration JSON."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MOCK = REPO_ROOT / "config/controller/device-configuration.mock.example.json"
DEFAULT_GDP_MOCK = REPO_ROOT / "config/controller/peak-events.example.json"


def _addon_options() -> dict[str, Any]:
    config_path = REPO_ROOT / "controller/config.yaml"
    with config_path.open(encoding="utf-8") as config_file:
        config = yaml.safe_load(config_file)
    return config.get("options", {})


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as source:
        return json.load(source)


def _write_json(path: Path, value: Any, *, force: bool = False) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"{path} already exists; choose another output or pass --force")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary_path.open("w", encoding="utf-8") as destination:
            json.dump(value, destination, indent=2, ensure_ascii=False)
            destination.write("\n")
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _configuration_day() -> int:
    return (datetime.now().astimezone().weekday() + 1) % 7


def fetch_configuration(base_url: str, building_id: str, day: int) -> Any:
    url = f"{base_url.rstrip('/')}/api/devices/{quote(building_id, safe='')}"
    response = requests.get(url, params={"day": day}, timeout=20)
    response.raise_for_status()
    configuration = response.json()
    if not isinstance(configuration, dict):
        raise ValueError("HEMS API response must be a JSON object")
    return configuration


def fetch_gdp_events(base_url: str, building_id: str) -> Any:
    url = f"{base_url.rstrip('/')}/api/peak-events/{quote(building_id, safe='')}"
    response = requests.get(url, timeout=20)
    response.raise_for_status()
    events = response.json()
    if not isinstance(events, list) or any(not isinstance(event, dict) for event in events):
        raise ValueError("HEMS GDP response must be a JSON array of event objects")
    return events


def _differences(real_value: Any, mock_value: Any, path: str = "") -> list[tuple[str, str, Any, Any]]:
    if isinstance(real_value, dict) and isinstance(mock_value, dict):
        differences: list[tuple[str, str, Any, Any]] = []
        for key in sorted(real_value.keys() | mock_value.keys()):
            child_path = f"{path}/{str(key).replace('~', '~0').replace('/', '~1')}"
            if key not in mock_value:
                differences.append((child_path, "missing-in-mock", real_value[key], None))
            elif key not in real_value:
                differences.append((child_path, "only-in-mock", None, mock_value[key]))
            else:
                differences.extend(_differences(real_value[key], mock_value[key], child_path))
        return differences

    if isinstance(real_value, list) and isinstance(mock_value, list):
        differences = []
        for index in range(max(len(real_value), len(mock_value))):
            child_path = f"{path}/{index}"
            if index >= len(mock_value):
                differences.append((child_path, "missing-in-mock", real_value[index], None))
            elif index >= len(real_value):
                differences.append((child_path, "only-in-mock", None, mock_value[index]))
            else:
                differences.extend(_differences(real_value[index], mock_value[index], child_path))
        return differences

    if type(real_value) is not type(mock_value) or real_value != mock_value:
        return [(path or "/", "different", real_value, mock_value)]
    return []


def _parse_pointer(pointer: str) -> list[str]:
    if not pointer.startswith("/"):
        raise ValueError("JSON Pointer must start with '/', e.g. /climate.salon/setpoint/value")
    return [
        component.replace("~1", "/").replace("~0", "~")
        for component in pointer[1:].split("/")
    ]


def _set_pointer_value(document: Any, pointer: str, value: Any) -> None:
    components = _parse_pointer(pointer)
    if not components:
        raise ValueError("The document root cannot be replaced")

    current = document
    for component in components[:-1]:
        try:
            current = current[int(component)] if isinstance(current, list) else current[component]
        except (KeyError, IndexError, ValueError, TypeError) as error:
            raise ValueError(f"JSON Pointer does not exist: {pointer}") from error

    leaf = components[-1]
    try:
        if isinstance(current, list):
            current[int(leaf)] = value
        else:
            if leaf not in current:
                raise ValueError(f"JSON Pointer does not exist: {pointer}")
            current[leaf] = value
    except (KeyError, IndexError, ValueError, TypeError) as error:
        if isinstance(error, ValueError) and str(error).startswith("JSON Pointer"):
            raise
        raise ValueError(f"JSON Pointer does not exist: {pointer}") from error


def _add_arguments(parser: argparse.ArgumentParser) -> None:
    subparsers = parser.add_subparsers(dest="command", required=True)
    addon_options = _addon_options()

    fetch_parser = subparsers.add_parser("fetch", help="fetch a real configuration from the HEMS API")
    fetch_parser.add_argument("--output", type=Path, required=True, help="new snapshot JSON path")
    fetch_parser.add_argument(
        "--base-url",
        default=os.getenv("HEMS_API_BASE_URL", addon_options.get("hems_api_base_url")),
    )
    fetch_parser.add_argument(
        "--building-id",
        default=os.getenv("BUILDING_ID", addon_options.get("building_id")),
    )
    fetch_parser.add_argument("--day", type=int, choices=range(7), default=_configuration_day())
    fetch_parser.add_argument("--force", action="store_true", help="replace an existing output file")

    fetch_gdp_parser = subparsers.add_parser("fetch-gdp", help="fetch real GDP events from the HEMS API")
    fetch_gdp_parser.add_argument("--output", type=Path, required=True, help="new GDP snapshot JSON path")
    fetch_gdp_parser.add_argument(
        "--base-url",
        default=os.getenv("HEMS_API_BASE_URL", addon_options.get("hems_api_base_url")),
    )
    fetch_gdp_parser.add_argument(
        "--building-id",
        default=os.getenv("BUILDING_ID", addon_options.get("building_id")),
    )
    fetch_gdp_parser.add_argument("--force", action="store_true", help="replace an existing output file")

    compare_parser = subparsers.add_parser("compare", help="compare real and mock configurations")
    compare_parser.add_argument("--real", type=Path, required=True, help="real configuration snapshot JSON")
    compare_parser.add_argument("--mock", type=Path, default=DEFAULT_MOCK, help="mock configuration JSON")
    compare_parser.add_argument("--max-diffs", type=int, default=200)

    compare_gdp_parser = subparsers.add_parser("compare-gdp", help="compare real and mock GDP event arrays")
    compare_gdp_parser.add_argument("--real", type=Path, required=True, help="real GDP snapshot JSON")
    compare_gdp_parser.add_argument(
        "--mock", type=Path, default=DEFAULT_GDP_MOCK, help="mock GDP events JSON"
    )
    compare_gdp_parser.add_argument("--max-diffs", type=int, default=200)

    copy_parser = subparsers.add_parser("copy", help="make an editable copy of a configuration")
    copy_parser.add_argument("--source", type=Path, required=True)
    copy_parser.add_argument("--output", type=Path, required=True)
    copy_parser.add_argument("--force", action="store_true", help="replace an existing output file")

    set_parser = subparsers.add_parser("set", help="change one existing value in a work copy")
    set_parser.add_argument("--file", type=Path, required=True)
    set_parser.add_argument("--pointer", required=True, help="JSON Pointer to an existing value")
    set_parser.add_argument("--value", required=True, help="replacement value written as JSON")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    _add_arguments(parser)
    args = parser.parse_args()

    try:
        if args.command == "fetch":
            if not args.base_url or not args.building_id:
                parser.error("fetch needs --base-url/HEMS_API_BASE_URL and --building-id/BUILDING_ID")
            configuration = fetch_configuration(args.base_url, args.building_id, args.day)
            _write_json(args.output, configuration, force=args.force)
            print(f"Saved day={args.day} configuration for {args.building_id} to {args.output}")
            return 0

        if args.command == "fetch-gdp":
            if not args.base_url or not args.building_id:
                parser.error("fetch-gdp needs --base-url/HEMS_API_BASE_URL and --building-id/BUILDING_ID")
            events = fetch_gdp_events(args.base_url, args.building_id)
            _write_json(args.output, events, force=args.force)
            print(f"Saved {len(events)} GDP event(s) for {args.building_id} to {args.output}")
            return 0

        if args.command in {"compare", "compare-gdp"}:
            differences = _differences(_read_json(args.real), _read_json(args.mock))
            print(f"Real: {args.real}\nMock: {args.mock}")
            if not differences:
                print("Configurations match.")
                return 0
            print(f"{len(differences)} difference(s):")
            for path, kind, real_value, mock_value in differences[: args.max_diffs]:
                print(f"\n{kind}: {path}\n  real: {json.dumps(real_value, ensure_ascii=False)}")
                print(f"  mock: {json.dumps(mock_value, ensure_ascii=False)}")
            if len(differences) > args.max_diffs:
                print(f"\n... {len(differences) - args.max_diffs} more difference(s) omitted")
            return 1

        if args.command == "copy":
            _write_json(args.output, _read_json(args.source), force=args.force)
            print(f"Created editable copy: {args.output}")
            return 0

        if args.command == "set":
            try:
                value = json.loads(args.value)
            except json.JSONDecodeError as error:
                raise ValueError("--value must be valid JSON (e.g. 21, true, null, or \"text\")") from error
            document = _read_json(args.file)
            _set_pointer_value(document, args.pointer, value)
            _write_json(args.file, document, force=True)
            print(f"Updated {args.pointer} in {args.file}")
            return 0
    except (FileNotFoundError, FileExistsError, json.JSONDecodeError, requests.RequestException, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
