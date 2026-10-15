"""The supplied academic layout uses comma-separated specialization labels."""


def specialization_labels(value: str) -> tuple[str, ...]:
    """Preserve raw text; expose exact labels without aliases or policy inference."""
    if not isinstance(value, str):
        raise ValueError("Specialization must be supplied text.")
    labels = tuple(label.strip() for label in value.split(","))
    if not all(labels) or len(set(labels)) != len(labels):
        raise ValueError("Specialization list contains empty or repeated labels.")
    return labels
