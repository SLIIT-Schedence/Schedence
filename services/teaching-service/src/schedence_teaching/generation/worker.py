"""One disposable spawned CP-SAT process per job; no database credentials.

Private IPC pickle files contain only service-created Python objects. They are
never accepted from callers or external uploads. JSON reports are private too.
"""

import asyncio
import copyreg
from dataclasses import dataclass
import json
import multiprocessing
import os
from pathlib import Path
import pickle
import traceback
from types import MappingProxyType

from schedence_teaching.persistence.snapshots import prepare_attempt, replay_snapshot
from schedence_teaching.solver import SolverStatus, solve_timetable


@dataclass(frozen=True)
class GenerationOutcome:
    result: object


class WorkerFailure(Exception):
    pass


def private_directory(path):
    """Artifacts stay in ignored Teaching local-data; never use request paths."""
    path = Path(path).resolve()
    local = Path(__file__).resolve().parents[3] / "local-data"
    if not path.is_relative_to(local.resolve()) or path == local.resolve():
        raise ValueError("generation artifacts require a private Teaching local-data directory")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _dump(value, path):
    # Existing prepared reports expose immutable mapping views. A private copy
    # in IPC is safe; independent validation always replays original contracts.
    with path.open("xb") as handle:
        writer = pickle.Pickler(handle, protocol=pickle.HIGHEST_PROTOCOL)
        writer.dispatch_table = copyreg.dispatch_table.copy()
        writer.dispatch_table[MappingProxyType] = lambda value: (dict, (dict(value),))
        writer.dump(value)


def execute_work(work, directory):
    """Reuse native solve, including coordinated reservations and union validation."""
    enrolled = None
    if work.evidence is not None:
        from .inputs import prepare_database_work
        from schedence_teaching.persistence.generation_inputs import build_database_generation
        from schedence_teaching.persistence.snapshots import content_digest
        rebuilt = prepare_database_work(work.evidence, work.request)
        if content_digest(rebuilt.document) != content_digest(work.document):
            raise ValueError("worker inputs differ from accepted detached snapshot")
        enrolled = build_database_generation(work.evidence, work.request.database_request()).enrolled
    if work.request.strategy == "COORDINATED_COHORTS":
        if enrolled is None:
            raise ValueError("coordinated generation requires validated enrolled database inputs")
        from schedence_teaching.pilot.coordinated_streams import solve_coordinated_cohorts
        result, stages = solve_coordinated_cohorts(enrolled, seconds_per_part=work.request.max_time_seconds)
        (directory / "coordinated-stages.json").write_text(json.dumps(stages, indent=2), encoding="utf-8")
    else:
        _, prepared, config, contexts = replay_snapshot(work.document)
        config = config.model_copy(update={"max_time_seconds": work.request.max_time_seconds})
        result = solve_timetable(prepared, config, **contexts)
    if enrolled is not None and result.status in {SolverStatus.FEASIBLE, SolverStatus.OPTIMAL}:
        from schedence_teaching.pilot.elective_validation import validate_elective_result
        validate_elective_result(enrolled, result)
    (directory / "solver-result.json").write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
    assessment = prepare_attempt(work.document, result)
    (directory / "independent-validation.json").write_text(assessment.validation_json, encoding="utf-8")
    return GenerationOutcome(result)


def _child(input_path, output_path, directory):
    # Existing decomposition writes progress to stdout. Keep it private.
    import sys
    with (Path(directory) / "worker.log").open("x", encoding="utf-8") as log:
        sys.stdout = sys.stderr = log
        try:
            with Path(input_path).open("rb") as handle:
                work = pickle.load(handle)
            outcome = execute_work(work, Path(directory))
            temporary = Path(output_path).with_suffix(".tmp")
            _dump(outcome, temporary)
            os.replace(temporary, output_path)
        except BaseException:
            traceback.print_exc(file=log)
            raise SystemExit(1)


class ProcessGenerationExecutor:
    """Hard wall timeout terminates and joins only this job's spawned process."""
    def __init__(self, directory):
        self.directory = private_directory(directory)

    async def run(self, work, job_id):
        directory = self.directory / str(job_id)
        directory.mkdir(exist_ok=False)
        (directory / "input-snapshot.json").write_text(work.document.model_dump_json(indent=2), encoding="utf-8")
        input_path, output_path = directory / "trusted-work.pickle", directory / "trusted-result.pickle"
        await asyncio.to_thread(_dump, work, input_path)
        process = multiprocessing.get_context("spawn").Process(target=_child,
            args=(str(input_path), str(output_path), str(directory)), daemon=True)
        try:
            process.start()
            async with asyncio.timeout(work.request.job_timeout_seconds):
                while process.is_alive():
                    await asyncio.sleep(0.05)
                await asyncio.to_thread(process.join)
            if process.exitcode != 0 or not output_path.exists():
                raise WorkerFailure("the isolated generation worker failed")
            with output_path.open("rb") as handle:
                return pickle.load(handle)
        finally:
            # A shutdown racing a wall timeout must not cancel process cleanup.
            cleanup = asyncio.create_task(asyncio.to_thread(_reap, process))
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
                raise


def _reap(process):
    if process.is_alive():
        process.terminate()
        process.join(5)
        if process.is_alive():
            process.kill()
            process.join()
    process.close()
