"""Byte ranges: which parts of a file are known to be good, and which are missing.

A range is (start, end) with end exclusive. Lists are kept sorted and merged.
"""


def merge(ranges):
    out = []
    for start, end in sorted(r for r in ranges if r[1] > r[0]):
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def missing(good, total):
    """The gaps in [0, total) not covered by `good`."""
    gaps, pos = [], 0
    for start, end in merge(good):
        if start > pos:
            gaps.append((pos, min(start, total)))
        pos = max(pos, end)
    if pos < total:
        gaps.append((pos, total))
    return [g for g in gaps if g[1] > g[0]]


def size(ranges):
    return sum(end - start for start, end in merge(ranges))


def clip(ranges, limit):
    return merge((s, min(e, limit)) for s, e in ranges if s < limit)
