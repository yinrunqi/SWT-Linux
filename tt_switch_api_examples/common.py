"""各示例共用的客户端配置。可用环境变量 TTSW_BASE_URL / TTSW_API_KEY 覆盖。"""

import os

from openai import OpenAI

BASE_URL = os.environ.get("TTSW_BASE_URL", "http://127.0.0.1:4200/native")
API_KEY = os.environ.get("TTSW_API_KEY", "sk-ttsw-local")

client = OpenAI(base_url=BASE_URL, api_key=API_KEY, timeout=600)
