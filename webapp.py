from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agents import Runner
from kyc_agent import build_agent, driver, logger, neo4j_mcp_server

agent = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    await neo4j_mcp_server.connect()
    global agent
    agent = build_agent()
    logger.info("WEBAPP: agent ready")
    yield
    await neo4j_mcp_server.cleanup()
    driver.close()


app = FastAPI(title="KYC Agent", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")


class ChatTurn(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[ChatTurn] = []


class ChatResponse(BaseModel):
    reply: str


@app.get("/")
async def index():
    return FileResponse("static/index.html")


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    history = [{"role": turn.role, "content": turn.content} for turn in req.history]
    result = await Runner.run(agent, history + [{"role": "user", "content": req.message}])
    return ChatResponse(reply=result.final_output)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("webapp:app", host="127.0.0.1", port=8000)
