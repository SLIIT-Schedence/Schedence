"""Module examination details management: update an existing module's exam details (Step 11b)."""

from app.module_details.memory import MemoryModuleDetailsStore
from app.module_details.rules import ExamDetails
from app.module_details.service import DetailsChange, ModuleDetailsService
from app.module_details.store import DatabaseModuleDetailsStore, ModuleDetailsStore, SavedTimetableRef

__all__ = ["DatabaseModuleDetailsStore", "DetailsChange", "ExamDetails", "MemoryModuleDetailsStore",
           "ModuleDetailsService", "ModuleDetailsStore", "SavedTimetableRef"]
