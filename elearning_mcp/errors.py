"""应用异常。"""


class SyncError(Exception):
    code = "query_failed"

    def __init__(self, message, *, code=None):
        super().__init__(message)
        if code is not None:
            self.code = code


class LoginRequired(SyncError):
    code = "login_required"


class SyncCancelled(SyncError):
    """The calling agent stopped an in-flight eLearning operation."""
