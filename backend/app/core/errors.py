from fastapi import HTTPException, status


class NotFound(HTTPException):
    def __init__(self, what: str = "Record"):
        super().__init__(status.HTTP_404_NOT_FOUND, f"{what} not found")


class Conflict(HTTPException):
    def __init__(self, detail: str):
        super().__init__(status.HTTP_409_CONFLICT, detail)


class Forbidden(HTTPException):
    def __init__(self, detail: str = "You do not have permission to perform this action"):
        super().__init__(status.HTTP_403_FORBIDDEN, detail)


class Unprocessable(HTTPException):
    def __init__(self, detail: str):
        super().__init__(422, detail)


class BusinessConflict(HTTPException):
    """An *expected* business outcome (insufficient stock, version conflict, duplicate posting...). Counted
    separately from system failures; load tests treat these as valid responses (HTTP 409)."""

    def __init__(self, code: str, message: str, **extra):
        super().__init__(status.HTTP_409_CONFLICT, {"code": code, "message": message, **extra})
        self.code = code


class InsufficientStock(BusinessConflict):
    def __init__(self, item_id: int, requested, available):
        super().__init__("insufficient_stock", f"Requested {requested} but only {available} is available",
                         item_id=item_id, requested=str(requested), available=str(available))
