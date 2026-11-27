"""ReqCheck: статическое ревью документов требований.

from reqcheck import validate, Options
report = validate(open("spec.pdf", "rb").read(), "spec.pdf", Options(max_cost_usd=0.5))
report.save("out/review.pdf")
"""

__version__ = "0.1.0"

__all__ = ["Event", "Options", "Report", "validate", "__version__"]


def __getattr__(name: str):
    # Ленивый импорт: `import reqcheck` не тянет pydantic-схемы и SDK модели, пока они не нужны
    if name in ("validate", "Options", "Event"):
        from reqcheck import api

        return getattr(api, name)
    if name == "Report":
        from reqcheck.schemas import Report

        return Report
    raise AttributeError(name)
