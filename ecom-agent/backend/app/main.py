from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
import logging
from app.config import UPLOAD_DIR
from app.models.user import User
from app.models.product import Product
from app.models.order import Order
from app.models.review import Review
from app.models.diagnosis_report import DiagnosisReport
from app.models.alert_notification import AlertNotification
from app.observability.models import AgentRun, AgentRunStep, AgentFeedback
from app.database import Base, engine
from app.api import api
from starlette.middleware.cors import CORSMiddleware
from app.common.exceptions import (
    BusinessException,
    business_exception_handler,
    http_excpetion_hadler,
    validation_excpetion_hadler,
    global_excpetion_hadler,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

Base.metadata.create_all(bind=engine)  # 自动帮我们创数据库表


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动项目开启异步的扫描任务
    yield


app = FastAPI(lifespan=lifespan)

origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "https://desktop-qkgqim0.tail717559.ts.net",  # Tailscale Funnel 公网通道
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,  # 允许的前端源，不要直接写 ["*"]
    allow_origin_regex=r"https://([a-z0-9-]+\.)*(workbuddy\.cn|workbuddy\.link|ts\.net)",  # WorkBuddy 托管前端域名
    allow_credentials=True,  # ✅ 关键：允许前端携带 Authorization token
    allow_methods=["*"],  # 允许所有请求方法 GET POST PUT DELETE OPTIONS
    allow_headers=["*"],  # 允许所有请求头（包含Authorization）
)

app.include_router(api)

# 注册异常处理器
app.add_exception_handler(BusinessException, business_exception_handler)
app.add_exception_handler(HTTPException, http_excpetion_hadler)
app.add_exception_handler(RequestValidationError, validation_excpetion_hadler)
# 全局的异常兜底，必须放在最后注册！！
app.add_exception_handler(Exception, global_excpetion_hadler)

# 挂载静态的文件目录  http://127.0.0.1:8000/uploads/xxx.jpg
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


@app.get("/")
def root():
    return {"message": "FastAPI工程正在运行中"}
