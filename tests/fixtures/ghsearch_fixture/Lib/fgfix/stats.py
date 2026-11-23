"""Fixture module for the GitHub-pool search tests (S2). Read as text, never run."""


def running_median(values, window):
    out = []
    buf = []
    for v in values:
        buf.append(v)
        if len(buf) > window:
            buf.pop(0)
        ordered = sorted(buf)
        mid = len(ordered) // 2
        if len(ordered) % 2:
            out.append(ordered[mid])
        else:
            out.append((ordered[mid - 1] + ordered[mid]) / 2)
    return out


class Window:
    def __init__(self, size):
        self.size = size
        self.items = []

    def push(self, item):
        self.items.append(item)
        while len(self.items) > self.size:
            self.items.pop(0)
        return len(self.items)
