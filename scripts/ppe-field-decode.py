#!/usr/bin/env python3
"""Decode AX PPE r4 ADD failures without interpreting them as packet loss."""

import argparse
from collections import Counter, defaultdict
import ipaddress
import json
from pathlib import Path
import sys


def bits(word, offset, length):
    return (word >> offset) & ((1 << length) - 1)


def read_events(path):
    header = None
    events = []
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        columns = line.split()
        if columns[:3] == ["sequence", "boottime_ns", "errno"]:
            header = columns
            required = {
                "command_id", "result_valid", "result", "expected_index", "index_mode",
                "op_type", "request_issued", "entry_words", "host_words", "pending",
                "cookie", "generation", "iif", "oif", "l3", "l4", "result_rejected",
                *(f"entry{i}" for i in range(9)), *(f"host{i}" for i in range(5)),
            }
            if not required.issubset(header):
                raise ValueError(f"{path}:{number}: unsupported failure-record layout")
            continue
        if not columns or not columns[0].isdigit():
            continue
        if header is None or len(columns) != len(header):
            raise ValueError(f"{path}:{number}: incomplete failure record")
        row = dict(zip(header, columns))
        try:
            words = [int(row[f"entry{i}"], 16) for i in range(9)]
            hosts = [int(row[f"host{i}"], 16) for i in range(5)]
            if any(not 0 <= word <= 0xFFFFFFFF for word in words + hosts):
                raise ValueError("entry word exceeds 32 bits")
            entry = sum(word << (32 * i) for i, word in enumerate(words))
            host = sum(word << (32 * i) for i, word in enumerate(hosts))
            v6 = bool(bits(entry, 1, 1))
            if int(row["entry_words"]) != (9 if v6 else 5) or int(row["host_words"]) != (5 if v6 else 3):
                raise ValueError("record does not contain a complete IPv4/IPv6 ADD key")
            if int(row["l3"], 16) != (0x86DD if v6 else 0x0800):
                raise ValueError("entry family disagrees with rule family")
            address = ipaddress.IPv6Address if v6 else ipaddress.IPv4Address
            source = str(address(bits(host, 22 if v6 else 32, 128 if v6 else 32)))
            destination = str(address(bits(entry, 140 if v6 else 76, 128 if v6 else 32)))
            protocol = {1: "TCP", 2: "UDP"}.get(bits(entry, 16, 2), "OTHER")
            if protocol in ("TCP", "UDP") and int(row["l4"]) != {"TCP": 6, "UDP": 17}[protocol]:
                raise ValueError("entry protocol disagrees with rule protocol")
            result = int(row["result"], 16)
            confirmed = (int(row["op_type"]) == 0 and int(row["index_mode"]) == 0
                         and int(row["request_issued"]) == 1 and int(row["result_valid"]) == 1
                         and int(row["result_rejected"]) == 0 and int(row["pending"]) == 0
                         and bool(result & 0x10) and (result & 15) == int(row["command_id"]))
            events.append({
                "sequence": int(row["sequence"]), "boottime_ns": int(row["boottime_ns"]),
                "errno": int(row["errno"]), "family": 6 if v6 else 4, "protocol": protocol,
                "source": source, "sport": bits(entry, 108, 16) if protocol != "OTHER" else None,
                "destination": destination, "dport": bits(entry, 124, 16) if protocol != "OTHER" else None,
                "action": {0: "FORWARD", 1: "SNAT", 2: "DNAT", 3: "ROUTE", 4: "BRIDGE",
                           5: "DROP", 6: "CPU"}.get(bits(entry, 29, 3), "UNKNOWN"),
                "cookie": row["cookie"], "generation": int(row["generation"]),
                "iif": int(row["iif"]), "oif": int(row["oif"]),
                "priority": bits(entry, 63, 5), "service_code": bits(entry, 68, 8),
                "confirmed_hardware_hash_add_fail": confirmed, "raw": row,
            })
        except ValueError as error:
            raise ValueError(f"{path}:{number}: {error}") from error
    if header is None:
        raise ValueError(f"{path}: no AX PPE r4 failure header found")
    return events


def tuple_key(event):
    return tuple(event[name] for name in ("family", "protocol", "source", "sport", "destination", "dport"))


def summarize(events):
    groups = defaultdict(list)
    for event in events:
        groups[tuple_key(event)].append(event)
    retries = []
    for key, rows in groups.items():
        if len(rows) < 2:
            continue
        rows.sort(key=lambda row: row["boottime_ns"])
        retries.append({"directed_tuple": key, "sequences": [row["sequence"] for row in rows],
                        "interval_seconds": [(b["boottime_ns"] - a["boottime_ns"]) / 1e9
                                             for a, b in zip(rows, rows[1:])]})
    return {"events": len(events), "unique_directed_tuples": len(groups),
            "protocol_counts": dict(Counter(event["protocol"] for event in events)),
            "family_protocol_action_counts": dict(Counter(
                f'IPv{event["family"]}/{event["protocol"]}/{event["action"]}' for event in events)),
            "confirmed_hardware_hash_add_fail": sum(event["confirmed_hardware_hash_add_fail"] for event in events),
            "repeated_tuples": retries}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="raw offload_failures file")
    parser.add_argument("--before", type=Path, help="earlier offload_failures, from the SAME boot")
    args = parser.parse_args()
    try:
        events = read_events(args.input)
        selection = {"scope": "retained history, not necessarily capture-time failures"}
        if args.before:
            baseline = read_events(args.before)
            high = max((event["sequence"] for event in baseline), default=-1)
            earlier_time = max((event["boottime_ns"] for event in baseline), default=0)
            latest = max((event["sequence"] for event in events), default=-1)
            latest_time = max((event["boottime_ns"] for event in events), default=0)
            if latest < high or latest_time < earlier_time:
                raise ValueError("failure history moved backwards; do not compare across boots/resets")
            events = [event for event in events if event["sequence"] > high]
            selection = {"scope": "new retained failures since baseline", "baseline_sequence": high,
                         "sequence_increment": latest - high,
                         "records_no_longer_retained": max(0, latest - high - len(events)),
                         "requires_same_boot_id": True}
        json.dump({"selection": selection, "summary": summarize(events), "events": events,
                   "boundary": "An ADD FAIL is not packet-loss proof. Counters are operation counts. "
                               "Hash-mode expected_index=U32_MAX is normal; destroy_miss is a software-cookie miss."},
                  sys.stdout, ensure_ascii=False, indent=2)
        print()
    except (OSError, UnicodeError, ValueError) as error:
        parser.exit(2, f"{error}\n")


if __name__ == "__main__":
    main()
