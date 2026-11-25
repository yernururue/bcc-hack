"""Коды выхода CLI и ошибки, которые на них отображаются (см. docs/02-trd.md, §3)."""

from enum import IntEnum


class ExitCode(IntEnum):
    OK = 0
    ERROR = 1
    BAD_INPUT = 2
    NO_TEXT_LAYER = 3
    AUTH = 4
    BUDGET = 5
    FAIL_ON = 10


class ReqCheckError(Exception):
    """Ошибка с понятным пользователю сообщением и кодом выхода."""

    exit_code: ExitCode = ExitCode.ERROR

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InputError(ReqCheckError):
    exit_code = ExitCode.BAD_INPUT


class NoTextLayerError(ReqCheckError):
    exit_code = ExitCode.NO_TEXT_LAYER


class AuthError(ReqCheckError):
    exit_code = ExitCode.AUTH


class BudgetExceededError(ReqCheckError):
    exit_code = ExitCode.BUDGET
