class CustomError(Exception):
    def __str__(self) -> str:
        return "custom text"


raise CustomError("raw text")
