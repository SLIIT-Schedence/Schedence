"""Explicit isolated durable worker process. Never runs migrations or seeds."""
import asyncio
from schedence_teaching.api.runtime import build_runtime
from .durable import DurableGenerationJobs


async def serve():
    runtime=build_runtime()
    try:
        if not isinstance(runtime.generations,DurableGenerationJobs):
            raise ValueError('configure DURABLE_ISOLATED mode before starting a durable worker')
        runtime.generations.start()
        await asyncio.Event().wait()
    finally:
        if runtime.generations is not None:await runtime.generations.close()
        for close in runtime.shutdown_hooks:await close()


if __name__=='__main__':
    from schedence_db.runtime import run
    try:run(serve())
    except KeyboardInterrupt:pass
