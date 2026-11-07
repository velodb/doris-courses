# Synthetic contract example

This small English/Kazakh tree demonstrates contract admission only. It is not
the Real-time Analytics pilot, a complete course translation, a native human
review or an installable workshop release. Its `source_ref` records the contract
development base; these synthetic files do not claim to exist at that commit.

The v1 catalog/course/quiz files validate independently. `package.v2.json`
binds their English identities, stable translation units, a two-cell Notebook,
localized lesson/quiz/UI assets and a shared SVG. Synthetic check records show
the metadata structure and explicitly claim no native human review.

Run from the repository root:

```bash
python tools/check_content_contract.py --root contracts/v2/examples
python tools/check_content_contract_v2.py --root contracts/v2/examples --allow-fixture
```

Without `--allow-fixture`, the v2 checker rejects this tree. Tests copy it to a
temporary directory and exercise integrity, publication and security failures.
See the [contract](../README.md) for production responsibilities and versioning.
