from typing import Callable, Awaitable, Optional, List
from datetime import datetime, timezone, timedelta
from pathlib import Path
from uuid import uuid4
from os import environ
from urllib.parse import urlparse
from base64 import b64encode
from re import escape, compile, IGNORECASE
from pprint import pprint

from requests import get
from fastapi import FastAPI, Request, Response, status, Depends
from fastapi.responses import JSONResponse, Response, PlainTextResponse, FileResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from pymongo import MongoClient, DESCENDING
from contextlib import asynccontextmanager
from redis.asyncio import from_url
from fastapi_limiter import FastAPILimiter
from fastapi_limiter.depends import RateLimiter

# Jinja2

def ip_to_uid(ip: Optional[str]) -> str:
    if not ip:
        return "-"

    return b64encode(ip.encode("utf-8")).decode("utf-8")[-11:]

def replace_ng_words(src: str, ng_words: List[str]) -> str:
    result = src

    for ng_word in ng_words:
        pattern = compile(escape(ng_word), IGNORECASE)
        if len(ng_word) == 1:
            result = pattern.sub("🆖", result)
        elif len(ng_word) >= 2:
            result = pattern.sub(ng_word[0] + "🆖" + ng_word[2:], result)

    return result

def content_to_linksets(content: str) -> str:
    pattern = compile(r"https?:\/\/\S+")
    groups = pattern.findall(content)
    return "\n".join(groups)

def is_over_n_hours(src: datetime, hours: int) -> bool:
    now = datetime.now()
    return now - src.replace(tzinfo=None) > timedelta(hours=hours)

# 初期化

ctx = {}

async def default_identifier(req: Request):
    cloudflare_ip = req.headers.get("CF-Connecting-IP")
    if cloudflare_ip:
        return cloudflare_ip.split(",")[0]

    forwarded = req.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0]

    return req.client.host + ":" + req.scope["path"]

@asynccontextmanager
async def lifespan(app: FastAPI):
    ctx["templates"] = Jinja2Templates("templates")

    ctx["templates"].env.filters["ip_to_uid"] = ip_to_uid
    ctx["templates"].env.filters["replace_ng_words"] = replace_ng_words
    ctx["templates"].env.filters["content_to_linksets"] = content_to_linksets
    ctx["templates"].env.filters["fromisoformat"] = datetime.fromisoformat
    ctx["templates"].env.filters["is_over_n_hours"] = is_over_n_hours

    ctx["mongo_client"] = MongoClient(
        environ.get("MONGO_URI", "mongodb://127.0.0.1:27017/"),
        username=environ.get("MONGO_USER"),
        password=environ.get("MONGO_PASSWORD")
    )

    #uuid重複する？考えすぎ？
    #ctx["mongo_client"].litey.notes.create_index("id", unique=True)
    ctx["mongo_client"].litey.ngs.create_index("word", unique=True)

    pprint(ctx)

    redis_uri = environ.get("REDIS_URI", "redis://127.0.0.1:6379/")
    redis_connection = from_url(redis_uri, encoding="utf8")
    await FastAPILimiter.init(redis_connection, identifier=default_identifier)
    yield
    ctx["mongo_client"].close()

    ctx.clear()

    await FastAPILimiter.close()

# スニペット

class LiteYItem(BaseModel):
    content: str

class LiteYDeleteItem(BaseModel):
    id: str

class NGItem(BaseModel):
    word: str

def fastapi_serve(dir: str, ref: str, indexes: List[str] = ["index.html", "index.htm"]) -> Response:
    url_path = urlparse(ref or "/").path
    root = Path(dir)

    try_files = []

    if url_path.endswith("/"):
        try_files += [root / url_path.lstrip("/") / i for i in indexes]

    try_files += [root / url_path]

    try_files_tried = [t for t in try_files if t.is_file()]

    print(try_files, try_files_tried)

    if not try_files_tried:
        return PlainTextResponse("指定されたファイルが見つかりません", status.HTTP_404_NOT_FOUND)

    path = try_files_tried[0]

    print(path, "をサーブ中")

    return FileResponse(path)

def get_ip(req: Request) -> str:
    return req.headers.get("CF-Connecting-IP") or req.headers.get("X-Forwarded-For") or req.client.host

def get_litey_notes(id: str = None) -> List[dict]:
    if not id:
        cursor = ctx["mongo_client"].litey.notes.find({}, { "_id": False }).sort("date", DESCENDING)
        return list(cursor)

    return ctx["mongo_client"].litey.notes.find_one({ "id": id }, { "_id": False })

def get_ng_words() -> List[str]:
    cursor = ctx["mongo_client"].litey.ngs.find({}, { "_id": False })
    return [ng["word"] for ng in list(cursor) if "word" in ng]

# FastAPI

app = FastAPI(lifespan=lifespan)

@app.middleware("http")
async def cors_handler(req: Request, call_next: Callable[[Request], Awaitable[Response]]):
    res = await call_next(req)

    if req.url.path.startswith("/api/"):
        res.headers["Access-Control-Allow-Origin"] = "*"
        res.headers["Access-Control-Allow-Credentials"] = "true"
        res.headers["Access-Control-Allow-Methods"] = "*"
        res.headers["Access-Control-Allow-Headers"] = "*"

        if req.method == "OPTIONS":
            res.status_code = status.HTTP_200_OK

    return res

@app.get("/api/litey/get")
async def api_get(id: str = None):
    res = JSONResponse(get_litey_notes(id))
    res.headers["Cache-Control"] = f"public, max-age=60, s-maxage=60"
    res.headers["CDN-Cache-Control"] = f"max-age=60"
    return res

@app.post("/api/litey/post")
async def api_post(item: LiteYItem, req: Request):
    ctx["mongo_client"].litey.notes.insert_one({
        "id": str(uuid4()),
        "content": item.content,
        "date": datetime.now().astimezone(timezone.utc).isoformat(),
        "ip": get_ip(req)
    })

    return PlainTextResponse("OK")

@app.post("/api/litey/delete", dependencies=[Depends(RateLimiter(times=1, seconds=86400))])
async def api_delete(item: LiteYDeleteItem):
    ctx["mongo_client"].litey.notes.delete_one({
        "id": item.id
    })

    return PlainTextResponse("OK")

@app.get("/api/litey/image-proxy")
async def api_image_proxy(url: str):
    result = get(url, timeout=5, headers={
        "User-Agent": Path("user_agent.txt").read_text("UTF-8").rstrip("\n")
    })

    content = result.content
    media_type = result.headers.get("Content-Type")

    res = Response(content, media_type=media_type)
    res.headers["Cache-Control"] = f"public, max-age=3600, s-maxage=3600"
    res.headers["CDN-Cache-Control"] = f"max-age=3600"
    return res

@app.get("/api/ng/get")
async def api_ng_get():
    res = PlainTextResponse("\n".join(get_ng_words()))
    res.headers["Cache-Control"] = f"public, max-age=60, s-maxage=60"
    res.headers["CDN-Cache-Control"] = f"max-age=60"
    return res

@app.post("/api/ng/post")
async def api_ng_post(item: NGItem):
    ctx["mongo_client"].litey.ngs.insert_one({
        "word": item.word
    })

    return PlainTextResponse("OK")

@app.post("/api/ng/delete", dependencies=[Depends(RateLimiter(times=1, seconds=86400))])
async def api_ng_delete(item: NGItem):
    ctx["mongo_client"].litey.ngs.delete_one({
        "word": item.word
    })

    return PlainTextResponse("OK")

@app.get("/")
async def home(req: Request):
    res = ctx["templates"].TemplateResponse(req, "index.html", {
        "notes": get_litey_notes(),
        "ng_words": get_ng_words()
    })
    res.headers["Cache-Control"] = f"public, max-age=60, s-maxage=60"
    res.headers["CDN-Cache-Control"] = f"max-age=60"
    return res

@app.get("/{ref:path}")
async def static(ref: str = None):
    res = fastapi_serve("static", ref)
    res.headers["Cache-Control"] = f"public, max-age=3600, s-maxage=3600"
    res.headers["CDN-Cache-Control"] = f"max-age=3600"
    return res
