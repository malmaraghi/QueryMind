"""Loading and validating benchmark question files."""
import json
from typing import List, Optional

REQUIRED_FIELDS = ("id", "question", "gold_sql", "required_tables", "order_significant")


def load_questions(path: str, annotations_path: Optional[str] = None) -> List[dict]:
    """Load questions, merging sidecar annotations (by id) when given.

    The original ``gold_questions.json`` has no ``required_tables`` or
    ``order_significant``; those live in a separate annotations file so the
    original five-question file stays untouched.
    """
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    questions = data["questions"] if isinstance(data, dict) else data

    if annotations_path:
        with open(annotations_path, encoding="utf-8") as handle:
            annotations = {item["id"]: item for item in json.load(handle)["annotations"]}
        merged = []
        for question in questions:
            extra = annotations.get(question["id"])
            if extra is None:
                raise ValueError(f"No annotation for question id {question['id']} in {annotations_path}")
            merged.append({**question, **{k: v for k, v in extra.items() if k != "id"}})
        questions = merged

    validate_questions(questions)
    return questions


def validate_questions(questions: List[dict]) -> None:
    seen = set()
    for question in questions:
        missing = [field for field in REQUIRED_FIELDS if field not in question]
        if missing:
            raise ValueError(f"Question {question.get('id')} is missing fields: {missing}")
        if question["id"] in seen:
            raise ValueError(f"Duplicate question id: {question['id']}")
        seen.add(question["id"])
        if not question["required_tables"]:
            raise ValueError(f"Question {question['id']} has no required_tables")
        if not isinstance(question["order_significant"], bool):
            raise ValueError(f"Question {question['id']}: order_significant must be true/false")
        if not question["gold_sql"].strip().lower().startswith(("select", "with")):
            raise ValueError(f"Question {question['id']}: reference SQL must be a SELECT")


def select_questions(questions: List[dict], ids: Optional[List[int]] = None, limit: Optional[int] = None) -> List[dict]:
    if ids:
        wanted = set(ids)
        questions = [q for q in questions if q["id"] in wanted]
        missing = wanted - {q["id"] for q in questions}
        if missing:
            raise ValueError(f"Unknown question ids: {sorted(missing)}")
    if limit is not None:
        questions = questions[:limit]
    return questions
