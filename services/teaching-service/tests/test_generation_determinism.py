"""Sealed scoped /generations inputs are identical in every process (the accepting API and the spawned worker).

The worker rebuilds the detached snapshot and refuses any digest difference, so the scoped seal must not
depend on Python's per-process hash seed (set-valued configuration serialized in hash order).
"""

import os
import pickle
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROBE = """
import pickle, sys
sys.path.insert(0, {tests!r})
from test_product_generation import generation_request
from schedence_teaching.generation.inputs import prepare_database_work
from schedence_teaching.persistence.snapshots import content_digest
with open({path!r}, "rb") as handle:
    evidence = pickle.load(handle)  # Test-created fixture only.
print(content_digest(prepare_database_work(evidence, generation_request(evidence)).document))
"""


def digest(path, seed):
    environment = dict(os.environ, PYTHONHASHSEED=str(seed))
    output = subprocess.run([sys.executable, "-B", "-c", PROBE.format(tests=str(HERE), path=str(path))], env=environment,
                            capture_output=True, text=True, timeout=600, check=True)
    return output.stdout.strip().splitlines()[-1]


def test_sealed_scoped_generation_inputs_do_not_depend_on_the_hash_seed(tmp_path):
    from test_product_generation import product_evidence
    evidence = product_evidence()  # One sealed combined snapshot with set-valued configuration.
    assert evidence.document.resolved_policy_digest is not None
    path = tmp_path / "evidence.pickle"
    with path.open("wb") as handle:
        pickle.dump(evidence, handle)
    first, second = digest(path, 1), digest(path, 2)
    assert len(first) == 64 and first == second
