#!/usr/bin/env python3
"""Single RC01 layout contract and checked build/runtime projections."""
import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def require(value, message):
    if not value:
        raise ValueError(message)


def load_layout(path=None):
    path = Path(path) if path is not None else ROOT / 'config/rc01/layout.json'
    data = json.loads(path.read_text())
    require(data.get('schema') == 1 and data.get('layout_id') == 'rc01-identity-preserved-r1', 'unsupported layout contract')
    nand = data['nand']
    require((nand['bytes'], nand['erase_bytes'], nand['page_bytes'], nand['leb_bytes']) ==
            (268435456, 131072, 2048, 126976), 'unsupported NAND geometry')
    require((nand['bad_block_reserve_pebs'], nand['ubi_overhead_pebs']) == (40, 4), 'unexpected reserve policy')
    parts = sorted(data['partitions'].values(), key=lambda p: p['index'])
    require(len(parts) == 26 and [p['index'] for p in parts] == list(range(26)), 'partition count/order')
    end, names = 0, set()
    for p in parts:
        require(re.fullmatch(r'[A-Za-z0-9_:.-]{1,15}', p['name']) is not None, 'invalid partition name')
        require(p['name'].lower() not in names, 'duplicate partition name')
        names.add(p['name'].lower())
        require(all(type(p[k]) is int for k in ('index', 'offset', 'bytes', 'attributes')), 'non-integer partition field')
        require(p['offset'] == end and p['bytes'] > 0 and p['bytes'] % nand['erase_bytes'] == 0, 'non-contiguous or unaligned partition')
        require(0 <= p['attributes'] <= 0xffffffff, 'partition attributes out of range')
        end += p['bytes']
    require(end == nand['bytes'] - 2097152, 'unexpected NAND tail')
    require([(p['name'], p['offset'], p['bytes']) for p in parts[23:]] ==
            [('0:LICENSE', 24379392, 262144), ('bdata', 24641536, 262144), ('rootfs', 24903680, 241434624)],
            'unexpected identity-preserving layout')
    require(all(data['partitions'][key] == parts[index] for key, index in
                [('license', 23), ('bdata', 24), ('rootfs', 25)]), 'partition lookup mismatch')
    require((data['environment']['bytes'], data['environment']['erase_bytes'], data['environment']['range_blocks']) ==
            (65536, 131072, 2), 'unsupported environment map')
    require((data['ubi']['kernel_id'], data['ubi']['rootfs_id'], data['ubi']['data_id'], data['ubi']['minimum_data_bytes']) ==
            (0, 1, 2, 33554432), 'unsupported UBI contract')
    return data


def shell_projection(data):
    nand, ubi, env = data['nand'], data['ubi'], data['environment']
    result = '# Generated from config/rc01/layout.json; check with scripts/rc01-layout.py.\n'
    values = {'RC01_LAYOUT_ID': data['layout_id'], 'RC01_PARTITION_COUNT': len(data['partitions']),
              'RC01_NAND_ERASE': nand['erase_bytes'], 'RC01_NAND_PAGE': nand['page_bytes'],
              'RC01_ROOTFS_PEBS': data['partitions']['rootfs']['bytes'] // nand['erase_bytes'],
              'RC01_RESERVE_PEBS': nand['bad_block_reserve_pebs'] + nand['ubi_overhead_pebs'],
              'RC01_BAD_BLOCK_RESERVE': nand['bad_block_reserve_pebs'], 'RC01_LEB_BYTES': nand['leb_bytes'],
              'RC01_MIN_DATA_BYTES': ubi['minimum_data_bytes'], 'RC01_ENV_BYTES': env['bytes'],
              'RC01_ENV_ERASE': env['erase_bytes'], 'RC01_ENV_RANGE': env['range_blocks']}
    for key, value in values.items():
        result += f"{key}='{value}'\n"
    rows = [f"{p['name']}|{p['offset']}|{p['bytes']}" for p in sorted(data['partitions'].values(), key=lambda p: p['index'])]
    return result + "RC01_LAYOUT_PARTITIONS='" + '\n'.join(rows) + "'\n"


def make_projection(data):
    size = data['partitions']['rootfs']['bytes']
    require(size % 1024 == 0, 'image size is not KiB-aligned')
    return '# Generated from config/rc01/layout.json.\nRC01_ROOTFS_SIZE_KIB:=' + str(size // 1024) + '\n'


def check_generated(data, root=ROOT):
    for relative, expected in {
        'config/rc01/layout.mk': make_projection(data),
        'package/emortal/rc01-platform/files/rc01-layout.sh': shell_projection(data),
    }.items():
        require((Path(root) / relative).read_text() == expected, 'layout projection drift: ' + relative)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-generated', action='store_true')
    parser.add_argument('--generate', action='store_true')
    args = parser.parse_args()
    data = load_layout()
    if args.generate:
        (ROOT / 'config/rc01/layout.mk').write_text(make_projection(data))
        (ROOT / 'package/emortal/rc01-platform/files/rc01-layout.sh').write_text(shell_projection(data))
    check_generated(data)
    print(json.dumps({'layout_id': data['layout_id'], 'rootfs_bytes': data['partitions']['rootfs']['bytes'],
                      'contract_sha256': hashlib.sha256((ROOT / 'config/rc01/layout.json').read_bytes()).hexdigest()}))


if __name__ == '__main__':
    main()
