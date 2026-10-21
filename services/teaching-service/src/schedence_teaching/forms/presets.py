"""The teaching-preference schema is server-owned, independently of question wording."""

from .contracts import AvailabilityMatrix, Preset, Question, QuestionKind

PRESET_ID = "lecturer-teaching-preferences.v1"


def teaching_preferences() -> Preset:
    # Return fresh nested contracts; one caller cannot mutate another institution's preset.
    return Preset(id=PRESET_ID, title="Lecturer Teaching Preferences",
        description="Teaching day-part availability and preferences. Preferred hours are a preference, not a workload cap.",
        questions=(
            Question(id="system.teaching_availability", kind="LOCKED", label="Teaching availability",
                help_text="Choose one preference for each day and Morning/Afternoon. Exact times come from institution policy.",
                field_key="teaching_availability", field_type="availability_matrix", required=True,
                matrix=AvailabilityMatrix()),
            Question(id="system.module_preferences", kind="LOCKED", label="Module preferences",
                help_text="Identify preferred module offerings by their existing offering IDs; this does not change teaching eligibility.",
                field_key="module_preferences", field_type="module_preferences", module_reference="offering_id"),
            Question(id="system.preferred_weekly_teaching_hours", kind="LOCKED", label="Preferred weekly teaching hours",
                field_key="preferred_weekly_teaching_hours", field_type="number", minimum=0, maximum=168),
            Question(id="suggested.optional_requirements", kind="SUGGESTED", label="Optional requirements",
                help_text="Additional information for the Academic Admin; not automatically interpreted as scheduler constraints.",
                field_type="long_text"),
        ))


def validate_questions(preset_id, questions):
    """Reject forged locks and preserve every registered scheduler contract on every save."""
    ids = [question.id for question in questions]
    if len(ids) != len(set(ids)):
        raise ValueError("Question IDs must be unique within a template.")
    registered = {row.id: row for row in teaching_preferences().questions} if preset_id == PRESET_ID else {}
    if preset_id is not None and preset_id != PRESET_ID:
        raise ValueError("Unknown predefined template.")
    locked = {key: row for key, row in registered.items() if row.kind is QuestionKind.LOCKED}
    actual = {row.id: row for row in questions}
    for key, original in locked.items():
        candidate = actual.get(key)
        if candidate is None or candidate.model_dump(exclude={"label", "help_text"}) != original.model_dump(exclude={"label", "help_text"}):
            raise ValueError("Locked questions may change wording only; their scheduler schema cannot be changed or removed.")
    for question in questions:
        if question.kind is QuestionKind.LOCKED and question.id not in locked:
            raise ValueError("Unregistered locked question.")
        if question.kind is QuestionKind.SUGGESTED and (question.id not in registered or question.id in locked):
            raise ValueError("Unregistered suggested question.")
        if question.id in registered and question.kind is not registered[question.id].kind:
            raise ValueError("A registered question cannot change its ownership kind.")
        if question.kind is QuestionKind.CUSTOM and question.id.startswith(("system.", "suggested.")):
            raise ValueError("Custom questions cannot use reserved question IDs.")
