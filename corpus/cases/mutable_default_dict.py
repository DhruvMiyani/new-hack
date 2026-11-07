def tally(word, counts={}):
    counts[word] = counts.get(word, 0) + 1
    return counts
