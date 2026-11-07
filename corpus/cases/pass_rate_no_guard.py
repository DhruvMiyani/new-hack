def pass_rate(results):
    passed = [r for r in results if r]
    return 100 * len(passed) / len(results)
