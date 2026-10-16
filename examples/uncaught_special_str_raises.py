class CustomError(Exception):
    def __str__(self) -> str:
        raise ValueError("rendering failed")


raise CustomError("raw text")
