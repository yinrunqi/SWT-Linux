"""测试收尾：部分测试会真实连接 SolidWorks 新建零件，用完立即关闭文档。

SolidWorks 中累积大量打开的零件会耗尽内存和句柄，导致整机卡死。
连接真机的测试模块标 `pytestmark = pytest.mark.solidworks`，地址统一读 SW_API_URL：
服务不可用时跳过，每个用例结束都关闭全部文档；其余模块结束时也再关一次。
"""

from __future__ import annotations

import os

import pytest
import requests

SW_URL = os.environ.get("SW_API_URL", "http://localhost:5100").rstrip("/")


def pytest_configure(config):
    config.addinivalue_line("markers", "solidworks: 连接真实 SolidWorks 的测试，每个用例结束都关闭文档")


def _service_up() -> bool:
    try:
        return requests.get(f"{SW_URL}/api/ping", timeout=2).ok
    except requests.RequestException:
        return False


def _close_all() -> None:
    try:
        requests.post(f"{SW_URL}/api/close-all-documents", json={}, timeout=120)
    except requests.RequestException:
        pass


@pytest.fixture(scope="session")
def _sw_available() -> bool:
    return _service_up()


@pytest.fixture(autouse=True)
def _close_solidworks_documents_after_live_test(request, _sw_available):
    if request.node.get_closest_marker("solidworks") is None:
        yield
        return
    if not _sw_available:
        pytest.skip(f"SolidWorks 服务不可用（{SW_URL}）")
    yield
    _close_all()


@pytest.fixture(scope="module", autouse=True)
def _close_solidworks_documents_after_module(_sw_available):
    yield
    if _sw_available:
        _close_all()
