"""测试收尾：部分测试会真实连接 SolidWorks 新建零件，每个模块结束后统一关闭文档。

SolidWorks 中累积大量打开的零件会耗尽内存和句柄，导致整机卡死。
"""

from __future__ import annotations

import os

import pytest
import requests

SW_URL = os.environ.get("SW_API_URL", "http://localhost:5100").rstrip("/")


def _service_up() -> bool:
    try:
        return requests.get(f"{SW_URL}/api/ping", timeout=2).ok
    except requests.RequestException:
        return False


@pytest.fixture(scope="session")
def _sw_available() -> bool:
    return _service_up()


@pytest.fixture(scope="module", autouse=True)
def _close_solidworks_documents_after_module(_sw_available):
    yield
    if not _sw_available:
        return
    try:
        requests.post(f"{SW_URL}/api/close-all-documents", json={}, timeout=120)
    except requests.RequestException:
        pass
