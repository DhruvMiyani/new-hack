def prune_zeros(counts):
    for key in counts:
        if counts[key] == 0:
            del counts[key]
    return counts
