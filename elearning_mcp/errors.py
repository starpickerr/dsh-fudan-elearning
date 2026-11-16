"""应用异常。"""


class SyncError(Exception):
    code = "query_failed"

    def __init__(self, message, *, code=None):
        super().__init__(message)
        if code is not None:
            self.code = code


class LoginRequired(SyncError):
    code = "login_required"


class DownloadSizeMismatch(SyncError):
    code = 'download_size_mismatch'

    def __init__(self, expected, actual):
        super().__init__('下载字节数与学校标注大小不符。')
        self.expected_size = expected
        self.actual_size = actual


class SyncCancelled(SyncError):
    """The calling agent stopped an in-flight eLearning operation."""
