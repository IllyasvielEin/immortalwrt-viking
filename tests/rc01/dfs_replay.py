#!/usr/bin/env python3
"""Replay the complete qualcommbe backports maintenance stack, without downloads.

Use a NEW output directory under the worktree build_dir. Existing outputs are
never replaced. The archive must be the mac80211 recipe's pinned input.
"""
import argparse
import hashlib
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('archive', type=Path)
parser.add_argument('output', type=Path)
args = parser.parse_args()
root = Path(__file__).resolve().parents[2]
archive = args.archive.resolve()
expected = '6ec76a4cb0988b5382b2fc5053610a56ada90b8ef6a5a4f2807cd433badb9454'
with archive.open('rb') as archive_file:
    if hashlib.file_digest(archive_file, 'sha256').hexdigest() != expected:
        raise SystemExit('archive SHA256 differs from pinned mac80211 input')
output = args.output.resolve()
output.mkdir(parents=True, exist_ok=False)
subprocess.run(['tar', '--zstd', '-xf', str(archive), '-C', str(output)], check=True)
source = output / 'backports-7.2'
patches = root / 'package/kernel/mac80211/patches'
count = 0
with (output / 'replay.log').open('w') as log:
    for directory in ('build', 'subsys', 'ath', 'ath5k', 'ath9k', 'ath10k',
                      'ath11k', 'ath12k', 'rt2x00', 'mt7601u', 'mwl', 'brcm', 'rtl'):
        for patch in sorted((patches / directory).glob('*.patch')):
            if directory == 'ath11k' and patch.name.startswith('nss-'):
                continue
            result = subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(patch)],
                                    cwd=source, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True)
            log.write(str(patch.relative_to(root)) + '\n' + result.stdout)
            log.flush()
            if result.returncode:
                raise SystemExit(f'failed {patch.name}; see {output / "replay.log"}')
            count += 1
print(f'{count} patches, fuzz=0, zero rejects: {source}')
