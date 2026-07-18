from fastapi import FastAPI

from app.routers import auth

app = FastAPI(title="SMC/ICT Live Bot -- Phase 0 (Foundation)")

app.include_router(auth.router)


@app.get("/health")
def health():
    return {"status": "ok"}
