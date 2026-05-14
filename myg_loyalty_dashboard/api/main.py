from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from .db import db
from .redis_cache import cache
from .routes import analytics

app = FastAPI(
    title="myG Loyalty Analytics API",
    description="High-performance analytics API using FastAPI, Redis, and PostgreSQL",
    version="1.0.0"
)

# GZip Compression configuration
app.add_middleware(GZipMiddleware, minimum_size=1000)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Startup and Shutdown events
@app.on_event("startup")
async def startup():
    await db.connect()
    await cache.connect()

@app.on_event("shutdown")
async def shutdown():
    await db.disconnect()

# Health check
@app.get("/health")
async def health():
    return {"status": "healthy", "db": "connected", "redis": "connected" if cache.client else "disconnected"}

# Include routers
app.include_router(analytics.router, prefix="/api/v1")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
