import uvicorn

from src.config import settings
def sever_run() -> None:
    uvicorn.run(
        "src.app:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.RELOAD,
    )

def main() -> None:
    sever_run()


if __name__ == "__main__":
    main()
