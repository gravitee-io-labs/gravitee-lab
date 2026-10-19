"""The seeder's tests, run from the root of the repository with `python -m unittest discover -s seeder/tests -t seeder`.

What they share, the fake platform and the `Seed` they run against, is `testkit.py` in the folder above, which a catalog
imports too (`from testkit import Seed`). The files of a playground they read are the invented samples in `fixtures/samples/`,
or those of the folder that `LAB_PLAYGROUNDS` names.

The four builders below make YAML that asks the loader for pairs it should not copy (a `<<` key merges the pairs of a mapping
into another, and a mapping that merges one that has merged others makes the count grow): test_common.py counts them and
refuses them, test_seedlint.py reads them in the files of a stranger. A test never hands the loader a document that costs more
than a few thousand pairs: what would cost more is counted, or run in a process of its own with a limit.
"""


def chained(count, indent=""):
    """`count` mappings, each merging the one before it and adding a key: the pairs copied grow with the square of `count`."""
    lines = ["a0: &a0 {k0: 1}"] + [f"a{n}: &a{n} {{<<: *a{n - 1}, k{n}: 1}}" for n in range(1, count)]
    return "".join(indent + line + "\n" for line in lines)


def doubling(count, indent="", merge="<<"):
    """`count` mappings, each merging the one before it twice: the pairs double with each, 2**count in the last."""
    lines = ["a0: &a0 {k: 1}"] + [f"a{n}: &a{n} {{{merge}: [*a{n - 1}, *a{n - 1}]}}" for n in range(1, count + 1)]
    return "".join(indent + line + "\n" for line in lines)


def fanned(keys, merges, indent=""):
    """One mapping of `keys` pairs merged by `merges` others: the pairs copied are the product."""
    lines = ["base: &b {" + ", ".join(f"k{n}: 1" for n in range(keys)) + "}"] + [f"u{n}: {{<<: *b, own: {n}}}" for n in range(merges)]
    return "".join(indent + line + "\n" for line in lines)


def flowing(count):
    """The doubling mappings in one flow mapping on a line, which a variable of the seed service can hold."""
    return "{" + ", ".join(["a0: &a0 {k: 1}"] + [f"a{n}: &a{n} {{<<: [*a{n - 1}, *a{n - 1}]}}" for n in range(1, count + 1)]) + "}"
