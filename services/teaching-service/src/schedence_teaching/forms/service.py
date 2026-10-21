"""Draft-only application service. No scheduler, response or distribution side effects."""

from datetime import datetime, timezone
from uuid import uuid4

from schedence_teaching.api.errors import ApiError

from .contracts import FormTemplate
from .presets import teaching_preferences, validate_questions


class FormTemplates:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def validate(preset_id, questions):
        try:
            validate_questions(preset_id, questions)
        except ValueError as error:
            raise ApiError(422, "INVALID_FORM_SCHEMA", str(error)) from None

    async def get(self, institution, template_id):
        row = await self.store.get(institution, template_id)
        if row is None:
            raise ApiError(404, "NOT_FOUND", "Form template not found.")
        return row

    async def create(self, body, actor):
        if body.preset_id is not None and body.questions:
            raise ApiError(422, "INVALID_FORM_SCHEMA", "Create from the predefined template, then save your edits.")
        questions = teaching_preferences().questions if body.preset_id else body.questions
        self.validate(body.preset_id, questions)
        now = datetime.now(timezone.utc)
        row = FormTemplate(id=uuid4(), institution_id=body.institution_id, title=body.title,
            description=body.description, preset_id=body.preset_id, questions=questions,
            created_by=actor, updated_by=actor, created_at=now, updated_at=now)
        await self.store.create(row)
        return row

    async def save(self, template_id, body, actor):
        old = await self.get(body.institution_id, template_id)
        if old.revision != body.expected_revision:
            raise ApiError(409, "REVISION_CONFLICT", "The template changed; reload it before saving.")
        self.validate(old.preset_id, body.questions)
        row = FormTemplate.model_validate(old.model_dump() | dict(title=body.title, description=body.description,
            questions=body.questions, revision=old.revision + 1, updated_by=actor, updated_at=datetime.now(timezone.utc)))
        await self.store.replace(row, body.expected_revision)
        return row

    async def duplicate(self, template_id, body, actor):
        old = await self.get(body.institution_id, template_id)
        self.validate(old.preset_id, old.questions)
        now = datetime.now(timezone.utc)
        row = FormTemplate.model_validate(old.model_dump() | dict(id=uuid4(), revision=1,
            title=body.title or (old.title[:193] + " (copy)"), created_by=actor, updated_by=actor,
            created_at=now, updated_at=now))
        await self.store.create(row)
        return row
