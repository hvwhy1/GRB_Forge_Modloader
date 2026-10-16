"""Read small Forge v27 tables from private files; never deserialize game assets."""
import collections
import json
import struct
from pathlib import Path


def read_index(path):
    path = Path(path)
    size = path.stat().st_size
    with path.open('rb') as f:
        def read_at(offset, length):
            if offset < 0 or offset + length > size:
                raise ValueError('Forge table range is outside the file')
            f.seek(offset)
            data = f.read(length)
            if len(data) != length:
                raise ValueError('Truncated Forge table')
            return data
        head = read_at(0, 21)
        if head[:9] != b'scimitar\0' or struct.unpack_from('<I', head, 9)[0] != 27:
            raise ValueError('Expected a Breakpoint v27 Forge')
        header = struct.unpack_from('<q', head, 13)[0]
        expected = struct.unpack('<i', read_at(header, 4))[0]
        count, following = struct.unpack('<iq', read_at(header + 32, 12))
        if not 0 < count <= 100000 or not 0 < expected <= 10000000:
            raise ValueError('Invalid Forge entry/directory count')
        entries, directories, seen = [], [], set()
        for directory in range(count):
            if following in seen:
                raise ValueError('Circular Forge directory chain')
            seen.add(following)
            raw = read_at(following, 48)
            n, flags, table, following, first, last, info, lost = struct.unpack('<iiqqiiqq', raw)
            if not 0 <= n <= 10000000:
                raise ValueError('Invalid directory size')
            directory_entries = []
            for slot in range(n):
                offset, identity, length = struct.unpack('<qQi', read_at(table + slot * 20, 20))
                if offset < 0 or length < 0 or offset + length > size:
                    raise ValueError('Resource payload is outside the Forge')
                metadata = read_at(info + slot * 192, 192)
                if struct.unpack_from('<i', metadata)[0] != length:
                    raise ValueError('Entry size differs between tables')
                name = metadata[44:172].split(b'\0', 1)[0].decode('utf-8', errors='strict')
                row = dict(directory=directory, slot=slot, id=f'{identity:016X}',
                    type=struct.unpack_from('<I', metadata, 16)[0], name=name,
                    offset=offset, length=length, engineVersion=struct.unpack_from('<i', metadata, 12)[0],
                    revisionData=struct.unpack_from('<i', metadata, 20)[0],
                    revisionAttributes=struct.unpack_from('<i', metadata, 24)[0],
                    next=struct.unpack_from('<i', metadata, 28)[0],
                    previous=struct.unpack_from('<i', metadata, 32)[0],
                    parent=struct.unpack_from('<i', metadata, 36)[0],
                    timestamp=struct.unpack_from('<I', metadata, 40)[0],
                    sccData=struct.unpack_from('<i', metadata, 172)[0],
                    metaFileKey=f'{struct.unpack_from("<Q", metadata, 176)[0]:016X}',
                    sccAttributes=struct.unpack_from('<i', metadata, 184)[0],
                    hidden=struct.unpack_from('<i', metadata, 188)[0])
                directory_entries.append(row)
                entries.append(row)
            directories.append(dict(index=directory, count=n, first=first, last=last,
                flags=flags, lostAndFound=lost,
                lastLink=directory_entries[-1]['next'] if n else None))
        if len(entries) != expected or following != -1:
            raise ValueError('Forge count or directory chain tail is inconsistent')
    duplicates = [dict(id=k[0], type=k[1], count=v) for k, v in
        collections.Counter((e['id'], e['type']) for e in entries).items() if v > 1]
    return dict(path=str(path.resolve()), bytes=size, entries=entries,
                directories=directories, duplicateIdentities=duplicates)


def compare_private(original, staged):
    before, after = read_index(original), read_index(staged)
    identities = {(e['id'], e['type']) for e in before['entries']}
    added = [e for e in after['entries'] if (e['id'], e['type']) not in identities]
    return dict(source=before['path'], staged=after['path'],
        originalEntries=len(before['entries']), stagedEntries=len(after['entries']),
        originalDirectories=before['directories'], stagedDirectories=after['directories'],
        added=added, duplicateIdentities=after['duplicateIdentities'],
        suspiciousInheritedMetadata=[e for e in added if e['metaFileKey'] != '0000000000000000'
            or e['parent'] != 0 or e['hidden'] != 0 or e['sccData'] != 4 or e['sccAttributes'] != 4],
        gameplayVerified=False)


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source'); p.add_argument('staged'); p.add_argument('--report', required=True)
    a = p.parse_args()
    result = compare_private(a.source, a.staged)
    Path(a.report).write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('added','suspiciousInheritedMetadata')}, indent=2))
