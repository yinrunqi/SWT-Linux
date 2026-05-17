"""
最小化测试：仅调用 new-part API，用于排查创建零件文档失败的问题。
"""
import requests

BASE = "http://localhost:5100"

def test_ping():
    """先测试服务是否可达"""
    try:
        r = requests.get(f"{BASE}/api/ping", timeout=5)
        print(f"[ping] 状态码={r.status_code}, 响应={r.text}")
        return r.status_code == 200
    except Exception as e:
        print(f"[ping] 连接失败: {e}")
        return False

def test_new_part():
    """测试新建零件"""
    try:
        r = requests.post(f"{BASE}/api/new-part", timeout=30)
        print(f"[new-part] 状态码={r.status_code}, 响应={r.text}")
        return r.status_code == 200
    except Exception as e:
        print(f"[new-part] 请求失败: {e}")
        return False

if __name__ == "__main__":
    print("=" * 50)
    print("排查测试：new-part API")
    print("=" * 50)

    print("\n1. 测试服务连通性 (ping)...")
    if not test_ping():
        print("\n❌ 服务不可达，请确认 HTTP API 服务已启动")
        exit(1)
    print("✅ 服务正常\n")

    print("2. 测试新建零件 (new-part)...")
    if test_new_part():
        print("\n✅ 新建零件成功！")
    else:
        print("\n❌ 新建零件失败")
        print("\n排查建议:")
        print("  1. 查看 C# 服务控制台输出的详细错误信息")
        print("  2. 确认 SolidWorks 窗口无弹窗/对话框")
        print("  3. 确认模板路径有效（服务启动时打印的路径）")
        print("  4. 尝试在 SolidWorks 中手动 文件→新建 看是否正常")
