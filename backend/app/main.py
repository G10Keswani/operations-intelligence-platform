from fastapi import FastAPI

app = FastAPI(
    title="Operations Intelligence Platform",
    version="1.0.0",
)


@app.get("/")
def root():
    return {
        "message": "Operations Intelligence Platform API"
    }


@app.get("/health")
def health():
    return {
        "status": "healthy"
    }
