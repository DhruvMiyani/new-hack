def prune_zeros(counts):
    return {k: v for k, v in counts.items() if v != 0}
