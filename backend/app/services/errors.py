class ServiceError(Exception):
    """Business error the API turns into an HTTP response."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class NotFound(ServiceError):
    def __init__(self, what: str):
        super().__init__(404, f"{what} not found")


class Conflict(ServiceError):
    def __init__(self, message: str):
        super().__init__(409, message)


class BadRequest(ServiceError):
    def __init__(self, message: str):
        super().__init__(422, message)
