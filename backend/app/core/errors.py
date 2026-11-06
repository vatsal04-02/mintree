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
