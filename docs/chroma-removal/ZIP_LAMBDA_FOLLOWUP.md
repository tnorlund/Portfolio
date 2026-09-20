# Lambda ZIP packaging after Chroma removal

Current main has removed the Chroma package from the receipt image build
paths. That makes ZIP packaging worth evaluating, but it does not prove
that any function fits. A Dockerfile can install large native or transitive
dependencies without naming them, and the former 153 MB estimate came
from a different platform and dependency set.

No function changes package type in this follow-up. Container-to-ZIP
conversion remains separate work with a reviewed infrastructure preview.

## Measure built artifacts

Build the proposed function payload for its intended Lambda runtime and
architecture, including its resolved dependencies. Package it as a ZIP and
include **every attached layer** in the size check:

```sh
python3 scripts/lambda_zip_budget.py function.zip layer-one.zip layer-two.zip
```

The helper sums the uncompressed entries from all supplied ZIPs. It returns
0 only below the project's **200 MiB** budget, 1 at or above that budget,
and 2 for missing, empty, or invalid artifacts. It also reports AWS's
separate 250 MiB hard ceiling. Compressed upload size is not this limit.
AWS uses MB to mean 1,024 KB in this quota. See the
[Lambda package quotas](https://docs.aws.amazon.com/lambda/latest/dg/gettingstarted-limits.html).

The helper is a size check. It cannot establish that all dependencies or
layers were supplied, prove native-wheel compatibility, or validate the
handler. Save the exact commit, lock/resolution evidence, target runtime
and architecture, build command, artifact hashes, size report, and handler
test results with each proposed conversion.

## Conversion sequence

1. Start with the small upload/trigger functions and measure their actual
   payloads. Revisit merge, resegment, place, MCP, QA, and cache functions
   independently; their dependency sets differ.
2. Test imports and representative handler inputs in the intended Lambda
   Linux runtime. Size alone does not establish deployability.
3. Keep LayoutLM's PyTorch image as an image unless separate measurements
   justify a new design. SageMaker training is not a Lambda ZIP candidate.
4. Review a dev-stack preview for the specific function replacement,
   names/aliases, permissions, event-source mappings, and rollback path.
   Do not combine this with the Python runtime migration.
5. After the dev replacement, verify an invocation and its expected
   outputs before proposing the production replacement.

The former Dockerfile classifier and historical size constants are not
shipping gates. No current Linux payload-size measurements or package-type
conversions are claimed by this document.
